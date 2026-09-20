"""Regressões locais: executar com python -m unittest discover -s backend."""
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

class Regressoes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pasta = tempfile.TemporaryDirectory()
        with patch.object(config, 'BANCO', str(Path(cls.pasta.name) / 'teste.db')), patch('paho.mqtt.client.Client.connect_async'), patch('paho.mqtt.client.Client.loop_start'), patch('threading.Thread.start'):
            spec = importlib.util.spec_from_file_location('hge_teste', Path(__file__).with_name('app.py'))
            cls.modulo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.modulo)
        cls.api = cls.modulo.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.pasta.cleanup()

    def setUp(self):
        self.modulo.estado['ultima_telemetria'] = None
        self.modulo.ultima_recebida_monotonic = None
        self.modulo.alertas_ativos.clear()
        self.modulo.excesso_desde.clear()

    def enviar(self, dados):
        self.modulo.ao_receber(None, None, SimpleNamespace(payload=json.dumps(dados).encode()))

    def test_telemetria_invalida_nao_atualiza_estado(self):
        for dados in [[], None, {'circuitos':[None]}, {'circuitos':[{'id':1,'ligado':'false'}]}, {'circuitos':[{'id':1,'ligado':True,'corrente':2,'potencia':float('nan'),'energia_wh':1}]}]:
            self.enviar(dados)
            self.assertIsNone(self.modulo.estado['ultima_telemetria'])

    def test_alerta_dez_segundos_preserva_inicio_e_resolve(self):
        c = {1: {'ligado': True, 'potencia': 2540}}
        agora = datetime(2026,9,21,12)
        for instante in [100,109]:
            with patch.object(self.modulo.time, 'monotonic', return_value=instante):
                self.modulo.atualizar_alertas(c, agora)
            self.assertNotIn('potencia:1', self.modulo.alertas_ativos)
        with patch.object(self.modulo.time, 'monotonic', return_value=110):
            self.modulo.atualizar_alertas(c, agora)
        inicio = self.modulo.alertas_ativos['potencia:1']['desde']
        with patch.object(self.modulo.time, 'monotonic', return_value=112):
            self.modulo.atualizar_alertas(c, agora)
        self.assertEqual(inicio, self.modulo.alertas_ativos['potencia:1']['desde'])
        c[1]['potencia'] = 100
        self.modulo.atualizar_alertas(c, agora)
        self.assertNotIn('potencia:1', self.modulo.alertas_ativos)

    def test_offline_limpa_alertas(self):
        self.modulo.alertas_ativos['potencia:1'] = {'circuito':1}
        self.modulo.excesso_desde[1] = 1
        self.assertEqual(self.api.get('/api/alertas').json, [])
        self.assertFalse(self.modulo.excesso_desde)

    def test_reconexao_reinicia_contagem(self):
        self.modulo.ultima_recebida_monotonic = 1
        self.modulo.excesso_desde[1] = 1
        with patch.object(self.modulo.time, 'monotonic', return_value=100):
            self.enviar({'tensao':127, 'circuitos':[{'id':1,'ligado':True,'corrente':20,'potencia':2540,'energia_wh':1}]})
        self.assertNotIn('potencia:1', self.modulo.alertas_ativos)
        self.assertEqual(self.modulo.excesso_desde[1],100)

    def test_rotas_e_agendamento_invalido(self):
        for rota in ['/api/status','/api/circuitos','/api/resumo','/api/historico','/api/comandos','/api/agendamentos']:
            self.assertEqual(self.api.get(rota).status_code,200)
        self.assertEqual(self.api.post('/api/circuitos/99/comando',json={'ligado':True}).status_code,404)
        self.assertEqual(self.api.post('/api/circuitos/1/comando',json={'ligado':'true'}).status_code,400)
        self.assertEqual(self.api.post('/api/agendamentos',json={'circuito':[], 'horario':'22:00','ligado':False,'dias_semana':[0]}).status_code,404)

if __name__ == '__main__':
    unittest.main()
