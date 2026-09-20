import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request, send_from_directory

from config import (
    BANCO,
    CIRCUITOS,
    HORARIO_FUNCIONAMENTO,
    LIMITES_POTENCIA_W,
    MQTT_BROKER,
    MQTT_PORTA,
    TARIFA_KWH,
    TIMEOUT_ONLINE_S,
    TEMPO_LIMITE_ALERTA_S,
    TOPICO_BASE,
)


BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = BASE_DIR.parent / "dashboard"
BANCO_PATH = BASE_DIR / BANCO

app = Flask(__name__)
estado_lock = threading.Lock()
estado = {
    "mqtt_conectado": False,
    "ultima_telemetria": None,
    "tensao": 127.0,
    "circuitos": {
        circuito_id: {
            "id": circuito_id,
            "nome": nome,
            "ligado": False,
            "corrente": 0.0,
            "potencia": 0.0,
            "energia_wh": 0.0,
        }
        for circuito_id, nome in CIRCUITOS.items()
    },
}
excesso_desde = {}
alertas_ativos = {}
ultima_recebida_monotonic = None


def agora_iso():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@contextmanager
def conectar_banco():
    conexao = sqlite3.connect(BANCO_PATH, timeout=10)
    conexao.row_factory = sqlite3.Row
    try:
        with conexao:
            yield conexao
    finally:
        conexao.close()


def inicializar_banco():
    with conectar_banco() as conexao:
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS leituras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                circuito INTEGER NOT NULL,
                ligado INTEGER NOT NULL,
                corrente REAL NOT NULL,
                potencia REAL NOT NULL,
                energia_wh REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comandos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                circuito INTEGER NOT NULL,
                ligado INTEGER NOT NULL,
                origem TEXT NOT NULL DEFAULT 'manual'
            );
            CREATE TABLE IF NOT EXISTS agendamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                circuito INTEGER NOT NULL,
                horario TEXT NOT NULL,
                dias_semana TEXT NOT NULL,
                ligado INTEGER NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1,
                ultima_execucao TEXT
            );
            """
        )
        colunas = {linha[1] for linha in conexao.execute("PRAGMA table_info(comandos)")}
        if "origem" not in colunas:
            conexao.execute(
                "ALTER TABLE comandos ADD COLUMN origem TEXT NOT NULL DEFAULT 'manual'"
            )


def esp32_online():
    with estado_lock:
        ultima = estado["ultima_telemetria"]
    if not ultima:
        return False
    return (datetime.now().astimezone() - datetime.fromisoformat(ultima)).total_seconds() <= TIMEOUT_ONLINE_S


def registrar_comando(circuito, ligado, origem):
    with conectar_banco() as conexao:
        conexao.execute(
            "INSERT INTO comandos (ts, circuito, ligado, origem) VALUES (?, ?, ?, ?)",
            (agora_iso(), circuito, int(ligado), origem),
        )


def publicar_comando(circuito, ligado, origem):
    if not cliente_mqtt.is_connected():
        return False
    resultado = cliente_mqtt.publish(
        f"{TOPICO_BASE}/comando/{circuito}", "ON" if ligado else "OFF"
    )
    if resultado.rc != mqtt.MQTT_ERR_SUCCESS:
        return False
    registrar_comando(circuito, ligado, origem)
    return True


def dentro_horario_funcionamento(agora):
    if agora.weekday() not in HORARIO_FUNCIONAMENTO["dias_semana"]:
        return False
    horario = agora.strftime("%H:%M")
    return HORARIO_FUNCIONAMENTO["inicio"] <= horario < HORARIO_FUNCIONAMENTO["fim"]


def atualizar_alertas(circuitos, agora):
    agora_monotonic = time.monotonic()
    dentro_horario = dentro_horario_funcionamento(agora)
    with estado_lock:
        for circuito_id, circuito in circuitos.items():
            chave_potencia = f"potencia:{circuito_id}"
            chave_horario = f"horario:{circuito_id}"
            limite = LIMITES_POTENCIA_W[circuito_id]

            if circuito["ligado"] and circuito["potencia"] > limite:
                excesso_desde.setdefault(circuito_id, agora_monotonic)
                duracao = agora_monotonic - excesso_desde[circuito_id]
                if duracao >= TEMPO_LIMITE_ALERTA_S:
                    alertas_ativos[chave_potencia] = {
                        "id": chave_potencia,
                        "circuito": circuito_id,
                        "nome": CIRCUITOS[circuito_id],
                        "tipo": "potencia",
                        "mensagem": f"Potência acima do limite de {limite} W",
                        "valor": round(circuito["potencia"], 2),
                        "limite": limite,
                        "desde": alertas_ativos.get(chave_potencia, {}).get("desde", agora_iso()),
                    }
            else:
                excesso_desde.pop(circuito_id, None)
                alertas_ativos.pop(chave_potencia, None)

            if circuito["ligado"] and not dentro_horario:
                alertas_ativos.setdefault(
                    chave_horario,
                    {
                        "id": chave_horario,
                        "circuito": circuito_id,
                        "nome": CIRCUITOS[circuito_id],
                        "tipo": "fora_horario",
                        "mensagem": "Circuito ligado fora do horário de funcionamento",
                        "valor": None,
                        "limite": None,
                        "desde": agora_iso(),
                    },
                )
            else:
                alertas_ativos.pop(chave_horario, None)


def ao_conectar(cliente, dados_usuario, flags, codigo_motivo, propriedades):
    conectado = codigo_motivo == 0
    with estado_lock:
        estado["mqtt_conectado"] = conectado
    if conectado:
        cliente.subscribe(f"{TOPICO_BASE}/telemetria")


def ao_desconectar(cliente, dados_usuario, flags, codigo_motivo, propriedades):
    with estado_lock:
        estado["mqtt_conectado"] = False


def ao_receber(cliente, dados_usuario, mensagem):
    global ultima_recebida_monotonic
    try:
        dados = json.loads(mensagem.payload.decode("utf-8"))
        if not isinstance(dados, dict):
            return
        circuitos = dados["circuitos"]
        if not isinstance(circuitos, list):
            return
        ts = agora_iso()
        linhas = []
        atualizacoes = {}
        for item in circuitos:
            if not isinstance(item, dict) or type(item.get("id")) is not int or type(item.get("ligado")) is not bool:
                raise ValueError("Circuito inválido")
            circuito_id = item["id"]
            if circuito_id not in CIRCUITOS:
                continue
            atual = {
                "id": circuito_id,
                "nome": CIRCUITOS[circuito_id],
                "ligado": bool(item["ligado"]),
                "corrente": float(item["corrente"]),
                "potencia": float(item["potencia"]),
                "energia_wh": float(item["energia_wh"]),
            }
            atualizacoes[circuito_id] = atual
            if any(not math.isfinite(atual[campo]) or atual[campo] < 0 for campo in ("corrente", "potencia", "energia_wh")):
                raise ValueError("Medição inválida")
            linhas.append(
                (
                    ts,
                    circuito_id,
                    int(atual["ligado"]),
                    atual["corrente"],
                    atual["potencia"],
                    atual["energia_wh"],
                )
            )
        if not atualizacoes:
            return
        tensao = float(dados.get("tensao", 127.0))
        if not math.isfinite(tensao) or tensao < 0:
            raise ValueError("Tensão inválida")
        with estado_lock:
            instante = time.monotonic()
            if ultima_recebida_monotonic is None or instante - ultima_recebida_monotonic > TIMEOUT_ONLINE_S:
                excesso_desde.clear()
                alertas_ativos.clear()
            ultima_recebida_monotonic = instante
            estado["ultima_telemetria"] = ts
            estado["tensao"] = tensao
            estado["circuitos"].update(atualizacoes)
        atualizar_alertas(atualizacoes, datetime.now().astimezone())
        with conectar_banco() as conexao:
            conexao.executemany(
                """INSERT INTO leituras
                (ts, circuito, ligado, corrente, potencia, energia_wh)
                VALUES (?, ?, ?, ?, ?, ?)""",
                linhas,
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        app.logger.warning("Telemetria MQTT inválida ignorada")


cliente_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hge-backend-{time.time_ns():x}")
cliente_mqtt.on_connect = ao_conectar
cliente_mqtt.on_disconnect = ao_desconectar
cliente_mqtt.on_message = ao_receber


def iniciar_mqtt():
    try:
        cliente_mqtt.connect_async(MQTT_BROKER, MQTT_PORTA, keepalive=60)
        cliente_mqtt.loop_start()
    except OSError:
        app.logger.exception("Não foi possível iniciar o MQTT")


def executar_agendamentos():
    while True:
        agora = datetime.now().astimezone()
        horario = agora.strftime("%H:%M")
        chave_execucao = agora.strftime("%Y-%m-%dT%H:%M")
        dia_semana = agora.weekday()
        with conectar_banco() as conexao:
            agendamentos = conexao.execute(
                "SELECT * FROM agendamentos WHERE ativo = 1 AND horario = ?",
                (horario,),
            ).fetchall()
            for agendamento in agendamentos:
                dias = json.loads(agendamento["dias_semana"])
                if dia_semana not in dias or agendamento["ultima_execucao"] == chave_execucao:
                    continue
                if publicar_comando(
                    agendamento["circuito"], bool(agendamento["ligado"]), "agendamento"
                ):
                    conexao.execute(
                        "UPDATE agendamentos SET ultima_execucao = ? WHERE id = ?",
                        (chave_execucao, agendamento["id"]),
                    )
        time.sleep(1)


@app.get("/")
def dashboard():
    return send_from_directory(DASHBOARD_DIR, "index.html")


@app.get("/api/status")
def api_status():
    with estado_lock:
        mqtt_conectado = estado["mqtt_conectado"]
        ultima = estado["ultima_telemetria"]
    return jsonify(
        status="ok",
        projeto="HGE – Hub Gerenciador de Energia",
        mqtt_conectado=mqtt_conectado,
        esp32_online=esp32_online(),
        ultima_telemetria=ultima,
    )


@app.get("/api/circuitos")
def api_circuitos():
    online = esp32_online()
    with estado_lock:
        circuitos = [dict(valor, online=online) for valor in estado["circuitos"].values()]
    return jsonify(circuitos)


@app.get("/api/resumo")
def api_resumo():
    with estado_lock:
        circuitos = list(estado["circuitos"].values())
        tensao = estado["tensao"]
    potencia = sum(item["potencia"] for item in circuitos)
    energia = sum(item["energia_wh"] for item in circuitos)
    return jsonify(
        tensao=tensao,
        potencia_total_w=round(potencia, 2),
        energia_total_wh=round(energia, 3),
        custo_estimado_reais=round(energia / 1000 * TARIFA_KWH, 2),
        tarifa_kwh=TARIFA_KWH,
        circuitos_ligados=sum(item["ligado"] for item in circuitos),
        total_circuitos=len(circuitos),
    )


@app.get("/api/historico")
def api_historico():
    try:
        minutos = min(60, max(1, int(request.args.get("minutos", 10))))
    except ValueError:
        minutos = 10
    limite = datetime.now().astimezone().timestamp() - minutos * 60
    with conectar_banco() as conexao:
        linhas = conexao.execute("SELECT ts, circuito, potencia FROM leituras ORDER BY ts").fetchall()
    recentes = [linha for linha in linhas if datetime.fromisoformat(linha["ts"]).timestamp() >= limite]
    por_horario = {}
    for linha in recentes:
        por_horario.setdefault(linha["ts"], {})[str(linha["circuito"])] = linha["potencia"]
    horarios = list(por_horario)
    series = {str(i): [por_horario[ts].get(str(i), 0) for ts in horarios] for i in CIRCUITOS}
    total = [sum(series[str(i)][indice] for i in CIRCUITOS) for indice in range(len(horarios))]
    return jsonify(horarios=horarios, series=series, total=total, nomes={str(k): v for k, v in CIRCUITOS.items()})


@app.post("/api/circuitos/<int:circuito_id>/comando")
def api_comando(circuito_id):
    if circuito_id not in CIRCUITOS:
        return jsonify(erro="Circuito não existe"), 404
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict) or type(dados.get("ligado")) is not bool:
        return jsonify(erro="Corpo inválido; informe ligado como true ou false"), 400
    if not publicar_comando(circuito_id, dados["ligado"], "manual"):
        return jsonify(erro="MQTT desconectado"), 503
    return jsonify(status="comando enviado"), 202


@app.get("/api/comandos")
def api_comandos():
    try:
        limite = min(100, max(1, int(request.args.get("limite", 20))))
    except ValueError:
        limite = 20
    with conectar_banco() as conexao:
        linhas = conexao.execute(
            "SELECT ts, circuito, ligado, origem FROM comandos ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return jsonify([
        dict(ts=l["ts"], circuito=l["circuito"], nome=CIRCUITOS[l["circuito"]], ligado=bool(l["ligado"]), origem=l["origem"])
        for l in linhas
    ])


@app.get("/api/alertas")
def api_alertas():
    if not esp32_online():
        with estado_lock:
            excesso_desde.clear()
            alertas_ativos.clear()
        return jsonify([])
    with estado_lock:
        alertas = list(alertas_ativos.values())
    return jsonify(sorted(alertas, key=lambda item: (item["circuito"], item["tipo"])))


def serializar_agendamento(linha):
    return dict(
        id=linha["id"],
        circuito=linha["circuito"],
        nome=CIRCUITOS[linha["circuito"]],
        horario=linha["horario"],
        dias_semana=json.loads(linha["dias_semana"]),
        ligado=bool(linha["ligado"]),
        ativo=bool(linha["ativo"]),
    )


@app.get("/api/agendamentos")
def listar_agendamentos():
    with conectar_banco() as conexao:
        linhas = conexao.execute("SELECT * FROM agendamentos ORDER BY horario, id").fetchall()
    return jsonify([serializar_agendamento(linha) for linha in linhas])


@app.post("/api/agendamentos")
def criar_agendamento():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify(erro="Corpo JSON inválido"), 400
    circuito = dados.get("circuito")
    horario = dados.get("horario")
    dias = dados.get("dias_semana")
    ligado = dados.get("ligado")
    try:
        datetime.strptime(horario, "%H:%M")
    except (TypeError, ValueError):
        return jsonify(erro="Horário inválido; use HH:MM"), 400
    if type(circuito) is not int or circuito not in CIRCUITOS:
        return jsonify(erro="Circuito não existe"), 404
    if type(ligado) is not bool or not isinstance(dias, list) or not dias:
        return jsonify(erro="Informe ligado e ao menos um dia da semana"), 400
    if any(type(dia) is not int or dia < 0 or dia > 6 for dia in dias):
        return jsonify(erro="Dias devem ser números de 0 (segunda) a 6 (domingo)"), 400
    dias = sorted(set(dias))
    horario = datetime.strptime(horario, "%H:%M").strftime("%H:%M")
    with conectar_banco() as conexao:
        cursor = conexao.execute(
            "INSERT INTO agendamentos (circuito, horario, dias_semana, ligado) VALUES (?, ?, ?, ?)",
            (circuito, horario, json.dumps(dias), int(ligado)),
        )
        linha = conexao.execute("SELECT * FROM agendamentos WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(serializar_agendamento(linha)), 201


@app.delete("/api/agendamentos/<int:agendamento_id>")
def remover_agendamento(agendamento_id):
    with conectar_banco() as conexao:
        cursor = conexao.execute("DELETE FROM agendamentos WHERE id = ?", (agendamento_id,))
    if cursor.rowcount == 0:
        return jsonify(erro="Agendamento não existe"), 404
    return "", 204


inicializar_banco()
iniciar_mqtt()
threading.Thread(target=executar_agendamentos, daemon=True, name="agendador-hge").start()

if __name__ == "__main__":
    app.run(port=5000, debug=True, use_reloader=False)
