MQTT_BROKER = "broker.hivemq.com"
MQTT_PORTA = 1883
TOPICO_BASE = "hge/estacio-rp"

CIRCUITOS = {
    1: "Iluminação",
    2: "Climatização",
    3: "Tomadas",
}

TARIFA_KWH = 0.80
TIMEOUT_ONLINE_S = 10
BANCO = "hge.db"

# Limites adequados à demonstração com corrente simulada de até 20 A.
LIMITES_POTENCIA_W = {
    1: 1800,
    2: 2200,
    3: 2000,
}

# Segunda (0) a sábado (5), das 07:00 às 22:00.
HORARIO_FUNCIONAMENTO = {
    "dias_semana": [0, 1, 2, 3, 4, 5],
    "inicio": "07:00",
    "fim": "22:00",
}
TEMPO_LIMITE_ALERTA_S = 10
