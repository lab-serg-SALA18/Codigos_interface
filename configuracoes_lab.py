# -*- coding: utf-8 -*-
"""
Configurações centrais do laboratório NV.

São os valores base dos experimentos e configurações dos arquivos


Os parâmetros de cada MEDIDA (range de tempo, número de médias, potência...)
você NÃO edita aqui: eles aparecem como campos editáveis na interface gráfica
(interface_laboratorio.py) e ficam salvos automaticamente em
'ultima_configuracao.json', nesta mesma pasta, para não precisar redigitar
tudo toda vez.
"""

import os
import json

# ======================================================================= #
# 1) CAMINHOS DO SISTEMA — AJUSTE PARA O SEU PC
# ======================================================================= #

# Executável do Python 3.11 (o ambiente virtual que roda o NVserver.py / Qiskit).
PYTHON_311_EXE = r"C:\Users\lab\Desktop\NV\python3_11\Scripts\python.exe"

# Pasta onde ficam os códigos
PASTA_PROJETO = r"C:\Users\lab\Desktop\NV\Joao_pedro\Codigos_interface"

# Pasta raiz onde os DADOS de cada experimento são salvos. Cada experimento
# (Rabi, T1, HAN, ODMR...) ganha sua própria subpasta aqui dentro automaticamente
PASTA_DADOS_BASE = r"C:\Users\lab\Desktop\NV\Joao_pedro"

# Arquivo que a interface (re)escreve antes de cada medida pulsada, com os
# parâmetros escolhidos na tela. O NVserver.py (Python 3.11) lê esse arquivo
# assim que é iniciado — por isso ele é reescrito ANTES de o servidor subir.
ARQUIVO_PARAMETROS_SEQUENCIA = os.path.join(PASTA_PROJETO, "ParametrosSequencias.py")

# ======================================================================= #
# 2) TELEGRAM (aviso automático quando uma medida termina)
# ======================================================================= #
TELEGRAM_TOKEN = "8625624126:AAFuO7RBJYfC8te1yITXtIIGvdLh2txgjN0"
TELEGRAM_CHAT_ID = "5867724350"
TELEGRAM_ATIVO_PADRAO = True  # mude para False se quiser desligar os avisos

# ======================================================================= #
# 3) GERADOR DE MICROONDAS (GPIB / VISA)
# ======================================================================= #
MW_RESOURCE_NAME = "GPIB0::27::INSTR"

# ======================================================================= #
# 4) MAPA DE BITS DO PULSEBLASTER (ver pulsed_backend.py )
# ======================================================================= #
BIT_LASER = 0b000000000000000000000001
BIT_MW = 0b000000000000000000000010
BIT_CAMERA = 0b000000000001000000000000
FLAG_BASE = 0b111000000000000000000000  # 3 bits altos que a placa sempre exige

# Estado "tudo ligado continuamente" — é exatamente o que o ativador_camera.pb
# fazia manualmente (laser + chave de MW + trigger da câmera sempre em alto,
# usado para o ODMR, que é uma medida CW / onda contínua).
TUDO_LIGADO = FLAG_BASE | BIT_CAMERA | BIT_MW | BIT_LASER

# ======================================================================= #
# 5) PORTAS DOS SERVIDORES (NVserver.py)
# ======================================================================= #
PORTA_SPINAPI = 65432
PORTA_QISKIT = 65433

# ======================================================================= #
# 6) SALVAR IMAGEM INDIVIDUAL DE CADA FRAME?
# ======================================================================= #
# O InicializadorSequencias.py original salvava um .png para CADA frame
# (médias x pontos x tipos de foto — facilmente 5000+ arquivos por medida).
# Isso deixa a aquisição bem mais lenta e enche o disco, e os dados que
# realmente importam já ficam salvos nos .h5f (soma de todos os frames).
# Deixei desligado por padrão; mude para True se quiser o comportamento antigo.
SALVAR_IMAGENS_INDIVIDUAIS = False


# ======================================================================= #
# 7) EXPERIMENTOS DISPONÍVEIS E SEUS VALORES PADRÃO
# ======================================================================= #
# unidade_tempo: como o GeradorSequencias.py interpreta o TIME_ARRAY
# grandeza: o que o ajuste automático tenta extrair no final da medida



EXPERIMENTOS = {
    "ODMR": {
        "nome_exibicao": "ODMR",
        "descricao": "Espectro de ressonância em MW",
        "grandeza": None,
        "parametros_padrao": {
            "mw_freq_center": 2871.0,
            "mw_freq_range": 320.0,
            "mw_freq_step": 1.0,
            "naverage": 200,
            "mw_power": -20.0,
            "shutter_time_ms": 20.0,
        },
    },
    "ODMR_PULSADO": {
            "nome_exibicao": "ODMR pulsado",
            "descricao": "Varredura de MW com pulso pi",
            "grandeza": None,
            "parametros_padrao": {
                "mw_freq_center": 3012.2,
                "mw_freq_range": 30.0,
                "mw_freq_step": 0.5,
                "naverage": 20,
                "mw_power": 0.0,            
                "pi_pulse_time_ns": 110.0,  
                "measure_loops": 50,
                "laser_time_us": 600.0,
                "backend_photo_on": False,
            },
    },
    "RABI": {
        "nome_exibicao": "Rabi",
        "descricao": "Oscilação de Rabi — pulso pi",
        "grandeza": "Frequência de Rabi e e T2* (Rabi)",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 600,
            "time_step": 10,
            "frequency": 3012.2,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 50,
            "laser_time_us": 450,
            "dynamic_shutter": False,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
    "T1": {
        "nome_exibicao": "T1",
        "descricao": "Tempo de relaxação T1",
        "grandeza": "Tempo de relaxação T1 longitudinal T1",
        "unidade_tempo": "us",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 40000,
            "time_step": 1,
            "frequency": 3010.2,
            "mw_power": 0.0,
            "images_average": 1,
            "measure_loops": 50,
            "laser_time_us": 450,
            "backend_photo_on": False,
            "plotar_fit": False,
        },
    },
    "HAN": {
        "nome_exibicao": "Hahn Echo",
        "descricao": "Eco de Hahn — T2",
        "grandeza": "Tempo de coerência T2",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 20000,
            "time_step": 500,
            "frequency": 2871.0,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 150,
            "laser_time_us": 500,
            "pi_pulse_time_ns": 170,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
    "RAM": {
        "nome_exibicao": "Ramsey",
        "descricao": "Franjas de Ramsey — T2*",
        "grandeza": "Tempo de coerência T2*",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 3000,
            "time_step": 50,
            "frequency": 2871.0,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 150,
            "laser_time_us": 500,
            "pi_pulse_time_ns": 170,
            "dynamic_shutter": True,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
    "CPMG2": {
        "nome_exibicao": "CPMG-2",
        "descricao": "Dynamical decoupling - 2 pi",
        "grandeza": "T2 (CPMG-2)",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 20000,
            "time_step": 500,
            "frequency": 2871.0,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 150,
            "laser_time_us": 500,
            "pi_pulse_time_ns": 170,
            "dynamic_shutter": True,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
    "CPMG4": {
        "nome_exibicao": "CPMG-4",
        "descricao": "Dynamical decoupling - 4 pi",
        "grandeza": "T2 (CPMG-4)",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 40000,
            "time_step": 1000,
            "frequency": 2871.0,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 150,
            "laser_time_us": 500,
            "pi_pulse_time_ns": 170,
            "dynamic_shutter": True,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
    "CPMG8": {
        "nome_exibicao": "CPMG-8",
        "descricao": "Dynamical decoupling - 8 pi",
        "grandeza": "T2 (CPMG-8)",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 80000,
            "time_step": 2000,
            "frequency": 2871.0,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 150,
            "laser_time_us": 500,
            "pi_pulse_time_ns": 170,
            "dynamic_shutter": True,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
    "ESEEM": {
        "nome_exibicao": "ESEEM",
        "descricao": "Envelope de acoplamentos hiperfinosEnvelope de modulação do eco — acoplamentos hiperfinos",
        "grandeza": "Frequência de modulação",
        "unidade_tempo": "ns",
        "parametros_padrao": {
            "time_start": 0,
            "time_stop": 5000,
            "time_step": 20,
            "frequency": 2871.0,
            "mw_power": 0.0,
            "images_average": 150,
            "measure_loops": 150,
            "laser_time_us": 500,
            "pi_pulse_time_ns": 170,
            "dynamic_shutter": True,
            "backend_photo_on": False,
            "plotar_fit": True,
        },
    },
}

# Rótulos em português para cada campo, usados na interface.
ROTULOS_PARAMETROS = {
    "mw_freq_center": "Freq. central (MHz)",
    "mw_freq_range": "Faixa (MHz)",
    "mw_freq_step": "Passo (MHz)",
    "naverage": "Médias",
    "mw_power": "Potência MW (dBm)",
    "shutter_time_ms": "Shutter (ms)",
    "time_start": "Tempo inicial",
    "time_stop": "Tempo final",
    "time_step": "Passo de tempo",
    "frequency": "Frequência MW (MHz)",
    "images_average": "Médias (imagens)",
    "measure_loops": "Loops de medida",
    "laser_time_us": "Tempo de laser (us)",
    "pi_pulse_time_ns": "Pulso pi (ns)",
    "dynamic_shutter": "Shutter dinâmico",
    "backend_photo_on": "Foto de background",
    "plotar_fit" : "Plotar fit",
}

# ======================================================================= #
# 8) PERSISTÊNCIA DA ÚLTIMA CONFIGURAÇÃO USADA
# ======================================================================= #
_ARQUIVO_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ultima_configuracao.json")


def carregar_configuracao():
    """Carrega os últimos parâmetros usados de cada experimento (se existirem)."""
    salvo = {}
    if os.path.isfile(_ARQUIVO_CONFIG):
        try:
            with open(_ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
                salvo = json.load(f)
        except (json.JSONDecodeError, OSError):
            salvo = {}

    config = {}
    for chave, exp in EXPERIMENTOS.items():
        padrao = dict(exp["parametros_padrao"])
        padrao.update(salvo.get(chave, {}))
        config[chave] = padrao
    return config


def salvar_configuracao(config):
    """Salva os parâmetros atuais em disco, para a próxima vez que abrir a interface."""
    try:
        with open(_ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"[Config] Não foi possível salvar a configuração: {e}")
