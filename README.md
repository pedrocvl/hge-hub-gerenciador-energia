# HGE — Hub Gerenciador de Energia

Projeto extensionista do curso de Ciência da Computação do Centro Universitário Estácio de Ribeirão Preto. O HGE simula a medição e o controle remoto de três circuitos de um prédio: Iluminação, Climatização e Tomadas.

O sistema usa esta arquitetura:

`ESP32 no Wokwi → broker MQTT público → backend Flask + SQLite → dashboard web`

## Requisitos

- Python 3.10 ou mais recente
- Acesso à internet para o broker MQTT e para o Wokwi
- Google Chrome ou outro navegador moderno

## Instalação no Windows

Abra o PowerShell na pasta do projeto e execute:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
```

Se o PowerShell bloquear a ativação do ambiente virtual, execute uma vez na mesma janela:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Configuração

As configurações ficam em `backend/config.py`:

- `MQTT_BROKER` e `MQTT_PORTA`: endereço do broker MQTT;
- `TOPICO_BASE`: deve continuar igual a `hge/estacio-rp` no backend e no firmware;
- `TARIFA_KWH`: tarifa usada no custo estimado;
- `LIMITES_POTENCIA_W`: limite de alerta de cada circuito;
- `HORARIO_FUNCIONAMENTO`: dias e horários normais do prédio;
- `TEMPO_LIMITE_ALERTA_S`: tempo acima do limite antes de gerar um alerta.

O projeto usa um broker público. Para evitar interferência entre demonstrações de grupos diferentes, escolha um tópico-base exclusivo e altere o mesmo valor em `backend/config.py` e `firmware/sketch.ino`.

## Iniciar com o Wokwi

1. Inicie o backend:

   ```powershell
   python backend\app.py
   ```

2. Abra o projeto no Wokwi e carregue os arquivos da pasta `firmware/`: `sketch.ino`, `diagram.json` e `libraries.txt`.
3. Inicie a simulação e aguarde `MQTT conectado.` no Serial Monitor.
4. Abra [http://127.0.0.1:5000](http://127.0.0.1:5000) no navegador.
5. Gire os potenciômetros para mudar a corrente. O painel atualiza em até 2 segundos.

Não execute `simulador.py` ao mesmo tempo que o Wokwi, pois ambos publicam no mesmo tópico de telemetria e um sobrescreve os valores do outro.

## Plano B: iniciar sem o Wokwi

Use dois terminais com o ambiente virtual ativado. No primeiro:

```powershell
python backend\app.py
```

No segundo:

```powershell
python backend\simulador.py
```

Depois, acesse [http://127.0.0.1:5000](http://127.0.0.1:5000).

## Testes

Os testes não precisam de internet nem do broker:

```powershell
python -m unittest discover -s backend -v
```

## API e MQTT

As rotas principais são `/api/status`, `/api/circuitos`, `/api/resumo`, `/api/historico`, `/api/agendamentos`, `/api/alertas` e `/api/comandos`.

Tópicos MQTT:

- `hge/estacio-rp/telemetria`: telemetria do ESP32 para o backend;
- `hge/estacio-rp/comando/<n>`: comandos `ON` ou `OFF` enviados ao circuito.

## Equipe

- Pedro: backend, dashboard 
- Rogerio: firmware e Wokwi
- João Victor: documentação
- Professor responsável: Omar Sacilotto Donaires

Instituições parceiras do cenário de uso: CEU — Centro de Artes e Esportes Unificados, em Sertãozinho, e Centro Cultural Palace, em Ribeirão Preto.
