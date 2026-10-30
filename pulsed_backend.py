# -*- coding: utf-8 -*-
"""
pulsed_backend.py — traduz portas lógicas em pulsos para a SpinCore.

Roda dentro do NVserver.py (Python 3.11). Traduz as portas lógicas do
circuito Qiskit em pulsos de laser/MW/câmera para a SpinCore, e manda a
sequência final pro spinapi_server (dentro do próprio NVserver.py) via
socket na porta 65432.
"""


from ParametrosSequencias import (
    LASER_DELAY,
    PI_PULSE_TIME,
    MEASURE_LOOPS,
    LASER_TIME,
)
import socket
import json
import numpy as np
from spinapi import (
    CONTINUE,
    END_LOOP,
    LOOP,
    pb_core_clock,
    pb_get_error,
    pb_init,
    us,
    ns,
    ms,
    s
)


"""
all_off = 0b000000000000000000000000
laser_on = 0b000000000000000000000001
mw_on = 0b000000000000000000000010
camera_on = 0b000000000001000000000000
set_pulse_time = 0b111000000000000000000000
"""

# Tempo de queda do laser/AOM depois que o bit digital desliga -- o "irmão"
# do LASER_DELAY (que é o tempo de SUBIDA). Eu estava tendo um problema
# com 60ns em experimentos pulsados, quebrei a cabeça pra resolvar mas não
# consegui então só tinha adicionado 60s de delay pra compensar. Esse problema
# se resolveu sozinho magicamente, mas deixei a config aqui para caso ele 
# apareça dnv
LASER_FALL_TIME = 60.0 * ns # 60

# Tempo de "aquecimento" (ramp-up) do MW depois que o
# bit liga. É um atraso DIFERENTE do LASER_FALL_TIME acima -- esse aqui é
# do lado eletrônico da MW, não do laser -- mas o problema é o mesmo: um
# delay. De novo, se resolveu sozinho e eu zerei, mas deixei ai pelo mesmo motivo
MW_RAMP_TIME = 50.0 * ns # 50

def translate_circuit_to_pulses(circuit, typePhoto):
    # Traduz as portas lógicas do circuito em pulsos de laser e MW.
    pulse_sequence = [] # sequência de pulsos
    for instruction in circuit: # para cada instrução no circuito, adiciona na sequência
        gate = instruction.operation.name # pega o nome da instrução
        if gate == "x":  # Rotacoes pi (X gate ou porta NOT)
            pulse_sequence.append(mw_pulse(PI_PULSE_TIME))
        elif gate == "h":  # Rotacoes pi/2 (H gate ou porta Hadamard))
            pulse_sequence.append(mw_pulse(PI_PULSE_TIME / 2))
        elif gate == 'rx': # Rotacoes arbitrarias (RX gate)
            theta = instruction.operation.params[0]
            theta_time = abs(theta) / np.pi * PI_PULSE_TIME
            if theta_time > 0: pulse_sequence.append(mw_pulse(theta_time))
        elif gate == 'time_x': # Pulso de MW
            pulse_time = instruction.operation.params[0]
            if pulse_time > 0: pulse_sequence.append(mw_pulse(pulse_time))
        elif gate == "delay": # Espera (delay)
            duration = instruction.operation.params[0]
            if duration > 0:
                pulse_sequence.append(wait(_delay_para_unidades_spinapi(instruction.operation)))
        elif gate == "measure":  # Medicao
            pulse_sequence, circuit_time = measurement_pulse(pulse_sequence, typePhoto, LASER_TIME, MEASURE_LOOPS)

    return pulse_sequence, circuit_time


def _delay_para_unidades_spinapi(delay):
    """Converte um ``Delay`` do Qiskit para a unidade da SpinCore.
    """
    unidade = getattr(delay, "unit", None)
    fatores = {"ns": ns, "us": us, "ms": ms, "s": s}
    if unidade not in fatores:
        raise ValueError(
            "Unidade de delay do Qiskit não suportada: {!r}. "
            "Use ns, us, ms ou s.".format(unidade)
        )
    return float(delay.params[0]) * fatores[unidade]

def mw_pulse(time: int):
    return [0b111000000001000000000010, CONTINUE, 0, time + MW_RAMP_TIME]
def laser_pulse(time: int):
    return [0b111000000001000000000001, CONTINUE, 0, time]
def no_pulse(time: int):
    return [0b111000000001000000000000, CONTINUE, 0, time]
def wait(time: int):
    return [0b111000000000000000000000, CONTINUE, 0, time]
def start_camera_delay(delay: int):
    return [0b111000000001000000000000, CONTINUE, 0, delay]
def turn_on_measure_laser(time: int):
    return [0b111000000001000000000001, END_LOOP, 1, time]
def measurement_pulse(pulse_sequence, typePhoto: str, set_laser_time: int, average_loops: int):
    """
    Cria um pulso de laser para medicao.
    """
    pulse_sequence.insert(0, start_camera_delay(delay=3.0 * us))

    set_turning_on_laser_delay(pulse_sequence, average_loops)
    _inserir_intervalo_apos_laser(pulse_sequence, LASER_FALL_TIME)

    pulse_sequence.append(turn_on_measure_laser(time=set_laser_time))

    if typePhoto == "back":
        for pulse in pulse_sequence:
            pulse[0] &= ~0b11
    elif typePhoto == "mw_off":
        for pulse in pulse_sequence:
            pulse[0] &= ~0b10

    sum = 0
    for pulse in pulse_sequence:
        sum += pulse[-1]

    circuit_time = sum * average_loops * 10e-7
    return pulse_sequence, circuit_time



def set_turning_on_laser_delay(pulse_sequence, average_loops: int):
    pulses_time = sum(pulse[-1] for pulse in pulse_sequence[1:])

    pulse_sequence.insert(1, [0b111000000001000000000000, LOOP, average_loops, 400 + min(pulses_time, LASER_DELAY)])

    time_accumulated = 0
    index = len(pulse_sequence) - 1

    while index >= 2 and time_accumulated < LASER_DELAY:
        pulse = pulse_sequence[index]
        pulse[0] |= 0b000000000001000000000000  # trigger da câmera, isso é sempre seguro
 
        pulso_ativa_mw = bool(pulse[0] & 0b10)  # bit 1 = chave de MW ligada neste pulso
        if pulso_ativa_mw:
            # Pulso de MW (x, h, rx, time_x): NUNCA liga o laser junto.
            # Ligar o laser durante a rotação repolariza opticamente o spin
            # e apaga a rotação em andamento -- era isso que achatava o
            # começo da curva de Rabi (e reduz o contraste dos pulsos
            # pi/pi2 do HAN, RAM e CPMG, que têm o mesmo problema). Só pula
            # o pulso sem tocar nele; a janela do LASER_DELAY continua
            # sendo preenchida com o tempo livre ANTES dele.
            index -= 1
            continue
 
        pulse_i_duration = pulse[-1]
        time_left = LASER_DELAY - time_accumulated

        if pulse_i_duration <= time_left:
            # Todo o pulso cabe dentro da janela
            pulse[0] |= 0b000000000001000000000001  # ativa laser + câmera
            time_accumulated += pulse_i_duration
            index -= 1

        else:
            # Pulso ultrapassa a janela — precisa ser dividido
            before_delay = pulse_i_duration - time_left
            after_delay = time_left

            if before_delay > 0:
                pulse_before = [pulse[0] | 0b000000000001000000000000, pulse[1], pulse[2], before_delay]
                pulse_sequence[index] = pulse_before

            if after_delay > 0:
                pulse_with_laser_camera = [
                    pulse[0] | 0b000000000001000000000001,
                    pulse[1], pulse[2], after_delay
                ]
                pulse_sequence.insert(index + 1, pulse_with_laser_camera)

            time_accumulated += after_delay
            index -= 1
    # Complementar: se somou menos que LASER_DELAY, cria um pulso extra com laser+câmera
    remaining = LASER_DELAY - time_accumulated
    if remaining > 0:
        pulse_sequence.insert(index + 1, [0b111000000001000000000001, CONTINUE, 0, remaining])

    pulse_sequence[2:] = [[pulse[0] | 0b000000000001000000000000, *pulse[1:]] for pulse in pulse_sequence[2:] if pulse[-1] > 0]
    return pulse_sequence


def _inserir_intervalo_apos_laser(pulse_sequence, duracao):
    """
    Insere um pulso "escuro" (sem laser, sem MW, câmera mantida) logo após
    qualquer pulso com o laser ligado que seja seguido imediatamente por um
    pulso de MW. Ver o comentário de LASER_FALL_TIME acima para o motivo.

    Passa uma vez pela sequência inteira; funciona pra Rabi (um mw_pulse só)
    e também pra HAN/RAM/CPMG/ESEEM, onde o pulso de MW mais perto da
    medida pode ser o h/x final da sequência.
    """
    if duracao <= 0:
        return pulse_sequence

    i = 0
    while i < len(pulse_sequence) - 1:
        laser_atual = bool(pulse_sequence[i][0] & 0b1)
        mw_seguinte = bool(pulse_sequence[i + 1][0] & 0b10)
        if laser_atual and mw_seguinte:
            pulse_sequence.insert(i + 1, no_pulse(duracao))
        i += 1
    return pulse_sequence

def to_json_safe(obj):
    if isinstance(obj, list):
        return [to_json_safe(v) for v in obj]
    elif isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    else:
        return obj
_spinapi_sock = None # Variável global para manter a conexão viva

def send_pulses(pulse_sequence):
    global _spinapi_sock
    try:
        pulse_sequence = to_json_safe(pulse_sequence)
        comando = {
            "acao": "send_pulses",
            "pulse_sequence": pulse_sequence
        }

        # Conecta apenas se ainda não estiver conectado
        if _spinapi_sock is None:
            _spinapi_sock = socket.create_connection(('127.0.0.1', 65432), timeout=10)
            _spinapi_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        _spinapi_sock.sendall(json.dumps(comando).encode('utf-8'))
        resposta = _spinapi_sock.recv(8192)
        resposta = json.loads(resposta.decode('utf-8'))

        if resposta.get("status") != "ok":
            raise RuntimeError(f"[SpinAPI erro] {resposta.get('msg')}")
        return resposta

    except Exception as e:
        # Se der erro (ex: servidor caiu), fechamos o socket para tentar reabrir na próxima
        if _spinapi_sock is not None:
            _spinapi_sock.close()
            _spinapi_sock = None
        raise RuntimeError(f"Erro ao se comunicar com o servidor SpinAPI: {e}")

spincore_initialized = False
def initialize_spincore():
    global spincore_initialized
    if not spincore_initialized:
        result = pb_init()
        if result != 0:
            raise RuntimeError(pb_get_error())
        spincore_initialized = True
    else:
        print("Spincore já inicializado.")
    pb_core_clock(300.0)
