# -*- coding: utf-8 -*-
"""
Gerencia o subprocesso do NVserver.py, que precisa rodar em Python 3.11
(por causa do Qiskit), a partir da interface, que roda em Python 3.6
(por causa da câmera antiga). Substitui o passo manual de abrir um segundo
terminal, ativar o venv e digitar "python NVserver.py".

Também gera o ParametrosSequencias.py com os parâmetros escolhidos na tela
antes de cada medida pulsada (Rabi, T1, HAN, RAM, CPMGx, ESEEM).
"""

import os
import socket
import subprocess
import threading
import time

import configuracoes_lab as cfg


def escrever_parametros_sequencia(sequencia, params):
    """
    Recria o ParametrosSequencias.py com os valores escolhidos na interface.

    O NVserver.py (e tudo que ele importa: GeradorSequencias, pulsed_backend)
    lê esse arquivo assim que é iniciado — por isso ele precisa ser escrito
    ANTES de darmos spawn no subprocesso do Python 3.11.
    """
    unidade = cfg.EXPERIMENTOS[sequencia].get("unidade_tempo", "ns")
    time_array_str = "range({}, {}, {})".format(
        int(params["time_start"]), int(params["time_stop"]), int(params["time_step"])
    )
    agora = time.strftime("%d/%m/%Y %H:%M:%S")

    conteudo = (
        '"""\n'
        f"Gerado automaticamente pela interface do laboratório em {agora}.\n"
        "NAO EDITE ESTE ARQUIVO A MAO -- a proxima medida feita pela interface\n"
        "vai sobrescreve-lo. Para mudar os valores padrao de cada experimento,\n"
        "edite configuracoes_lab.py.\n"
        '"""\n'
        "from spinapi import ns, us, ms, s\n\n"
        f"SEQUENCY = {sequencia!r}\n\n"
        f'FREQUENCY = {float(params["frequency"])}\n'
        f"TIME_ARRAY = {time_array_str}   # unidade: {unidade}\n"
        f'MW_POWER = {float(params["mw_power"])}\n'
        f'IMAGES_AVERAGE = {int(params["images_average"])}\n\n'
        "LASER_DELAY = 350.0*ns\n"
        f'PI_PULSE_TIME = {float(params.get("pi_pulse_time_ns", 0.0))}*ns\n'
        f'MEASURE_LOOPS = {int(params["measure_loops"])}\n'
        f'LASER_TIME = {float(params["laser_time_us"])}*us\n\n'
        'INFOS = f"{FREQUENCY}mhz_{MW_POWER}dbm_{IMAGES_AVERAGE}averages_'
        '{TIME_ARRAY[0]}_{TIME_ARRAY[-1]}_{int(LASER_TIME)}ns_{MEASURE_LOOPS}loops_{SEQUENCY}"\n'
    )

    with open(cfg.ARQUIVO_PARAMETROS_SEQUENCIA, "w", encoding="utf-8") as f:
        f.write(conteudo)


def _ler_saida(processo, log_callback):
    try:
        for linha in iter(processo.stdout.readline, ""):
            if not linha:
                break
            if log_callback:
                log_callback(mensagem="[NVserver] " + linha.rstrip())
    except Exception:
        pass


def iniciar_servidor_nv(log_callback=None):
    """Sobe o NVserver.py no Python 3.11, em segundo plano, e devolve o Popen."""
    if not os.path.isfile(cfg.PYTHON_311_EXE):
        raise RuntimeError(f"Python 3.11 não encontrado em:\n{cfg.PYTHON_311_EXE}\n"
            "Ajuste PYTHON_311_EXE em configuracoes_lab.py.")
    
    if not os.path.isdir(cfg.PASTA_PROJETO):
        raise RuntimeError(f"Pasta do projeto não encontrada:\n{cfg.PASTA_PROJETO}\n"
            "Ajuste PASTA_PROJETO em configuracoes_lab.py.")
    
    if not os.path.isfile(os.path.join(cfg.PASTA_PROJETO, "NVserver.py")):
        raise RuntimeError(f"NVserver.py não encontrado dentro de:\n{cfg.PASTA_PROJETO}")

    processo = subprocess.Popen(
    [cfg.PYTHON_311_EXE, "NVserver.py"],
    cwd=cfg.PASTA_PROJETO,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
    )

    threading.Thread(
        target=_ler_saida,
        args=(processo, log_callback),
        daemon=True,
    ).start()

    return processo


def aguardar_portas_prontas(timeout=15):
    """Espera até que os dois servidores (SpinAPI e Qiskit) aceitem conexões de verdade."""
    portas = [cfg.PORTA_SPINAPI, cfg.PORTA_QISKIT]
    inicio = time.time()
    pendentes = set(portas)

    while pendentes and (time.time() - inicio) < timeout:
        for porta in list(pendentes):
            try:
                with socket.create_connection(("127.0.0.1", porta), timeout=0.5):
                    pendentes.discard(porta)
            except OSError:
                pass
        if pendentes:
            time.sleep(0.01)

    return not pendentes  # True se todas as portas responderam a tempo


def parar_servidor_nv(processo):
    """Encerra o NVserver.py de forma limpa (ou força, se ele não responder)."""
    if processo is None:
        return
    try:
        processo.terminate()
        processo.wait(timeout=5)
    except Exception:
        try:
            processo.kill()
        except Exception:
            pass
