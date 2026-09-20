import json
import random
import threading
import time

import paho.mqtt.client as mqtt

from config import MQTT_BROKER, MQTT_PORTA, TOPICO_BASE


lock = threading.Lock()
ligados = [True, True, True]
correntes = [5.87, 13.68, 3.91]
energias_wh = [0.0, 0.0, 0.0]
ultima_medicao = time.monotonic()


def montar_telemetria():
    global ultima_medicao
    agora = time.monotonic()
    horas = (agora - ultima_medicao) / 3600
    ultima_medicao = agora

    circuitos = []
    with lock:
        for indice in range(3):
            corrente = correntes[indice] if ligados[indice] else 0.0
            potencia = 127.0 * corrente
            energias_wh[indice] += potencia * horas
            circuitos.append(
                {
                    "id": indice + 1,
                    "ligado": ligados[indice],
                    "corrente": round(corrente, 2),
                    "potencia": round(potencia, 2),
                    "energia_wh": round(energias_wh[indice], 3),
                }
            )
    return json.dumps({"tensao": 127.0, "circuitos": circuitos})


def publicar(cliente):
    mensagem = montar_telemetria()
    cliente.publish(f"{TOPICO_BASE}/telemetria", mensagem)
    print(mensagem, flush=True)


def ao_conectar(cliente, dados_usuario, flags, codigo_motivo, propriedades):
    if codigo_motivo == 0:
        print("MQTT conectado.", flush=True)
        cliente.subscribe(f"{TOPICO_BASE}/comando/+")
        publicar(cliente)


def ao_receber(cliente, dados_usuario, mensagem):
    try:
        circuito = int(mensagem.topic.rsplit("/", 1)[1])
        comando = mensagem.payload.decode().strip().upper()
        if circuito not in (1, 2, 3) or comando not in ("ON", "OFF"):
            return
        with lock:
            ligados[circuito - 1] = comando == "ON"
        publicar(cliente)
    except (ValueError, UnicodeDecodeError):
        return


cliente = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"hge-simulador-{random.getrandbits(32):08x}",
)
cliente.on_connect = ao_conectar
cliente.on_message = ao_receber
cliente.connect(MQTT_BROKER, MQTT_PORTA, 60)
cliente.loop_start()

try:
    while True:
        publicar(cliente)
        time.sleep(2)
except KeyboardInterrupt:
    pass
finally:
    cliente.loop_stop()
    cliente.disconnect()
