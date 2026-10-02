# -*- coding: utf-8 -*-
"""
GeradorSequencias.py — gera os circuitos de cada sequência pulsada.

Roda dentro do NVserver.py (Python 3.11). Lê SEQUENCY do
ParametrosSequencias.py (escrito pela interface antes de cada medida) e
monta o circuito Qiskit correspondente.
"""

from ParametrosSequencias import SEQUENCY
from qiskit import QuantumCircuit
from qiskit_NV_Backend import NVCenterBackend
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from custom_gates import TimeXGate


nv_backend = NVCenterBackend()
pass_manager = generate_preset_pass_manager(0, backend=nv_backend)

# Uma medida percorre os mesmos tempos em cada média. O circuito já
# transpilado não muda entre essas repetições; somente a programação dos
# pulsos precisa ser enviada novamente para obter a próxima imagem.
_circuitos_transpilados = {}


def circuit_sequence(time_value, typePhoto):
    # Para mw_off/back, todas as sequências usam o circuito de tempo zero.
    # Compartilhar também essas entradas evita transpilações repetidas.
    tempo_chave = int(time_value) if typePhoto == "mw_on" else 0
    chave = (SEQUENCY.upper(), tempo_chave, typePhoto)
    transpiled_circuit = _circuitos_transpilados.get(chave)

    if transpiled_circuit is None:
        circuit = QuantumCircuit(1)

        if SEQUENCY.upper() == 'RABI':
            if typePhoto == 'mw_on':
                circuit.append(TimeXGate(pulse_time=time_value), [0])
                circuit.measure_all()
            else:
                circuit.append(TimeXGate(pulse_time=0), [0])
                circuit.measure_all()

        elif SEQUENCY.upper() == 'RAM':
            if typePhoto == 'mw_on':
                circuit.h(0)
                circuit.delay(time_value, unit='ns')
                circuit.h(0)
                circuit.measure_all()
            else:
                circuit.h(0)
                circuit.delay(0, unit='ns')
                circuit.h(0)
                circuit.measure_all()

        elif SEQUENCY.upper() == 'HAN':
            if typePhoto == 'mw_on':
                circuit.h(0)
                circuit.delay(time_value, unit='ns')
                circuit.x(0)
                circuit.delay(time_value, unit='ns')
                circuit.h(0)
                circuit.measure_all()
            else:
                circuit.h(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.h(0)
                circuit.measure_all()

        elif SEQUENCY.upper() == 'T1':
            if typePhoto == 'mw_on':
                circuit.delay(time_value, unit='us')
                circuit.measure_all()
            else:
                circuit.delay(1, unit='us')
                circuit.measure_all()

        elif SEQUENCY.upper() == 'CPMG2':
            if typePhoto == 'mw_on':
                circuit.h(0)
                circuit.delay(time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(time_value, unit='ns')
                circuit.h(0)
                circuit.measure_all()
            else:
                circuit.h(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.h(0)
                circuit.measure_all()

        elif SEQUENCY.upper() == 'CPMG4':
            if typePhoto == 'mw_on':
                circuit.h(0)
                circuit.delay(time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(time_value, unit='ns')
                circuit.h(0)
                circuit.measure_all()
            else:
                circuit.h(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.h(0)
                circuit.measure_all()

        elif SEQUENCY.upper() == 'CPMG8':
            if typePhoto == 'mw_on':
                circuit.h(0)
                circuit.delay(time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(2 * time_value, unit='ns')
                circuit.x(0)
                circuit.delay(time_value, unit='ns')
                circuit.h(0)
                circuit.measure_all()
            else:
                circuit.h(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.x(0)
                circuit.delay(0, unit='ns')
                circuit.h(0)
                circuit.measure_all()

        elif SEQUENCY.upper() == 'ESEEM':
            if typePhoto == 'mw_on':
                circuit.h(0)
                circuit.delay(1200, unit='ns')
                circuit.h(0)
                circuit.delay(time_value, unit='ns')
                circuit.h(0)
                circuit.delay(1200, unit='ns')
                circuit.h(0)
                circuit.measure_all()
            else:
                circuit.h(0)
                circuit.delay(1200, unit='ns')
                circuit.h(0)
                circuit.delay(0, unit='ns')
                circuit.h(0)
                circuit.delay(1200, unit='ns')
                circuit.h(0)
                circuit.measure_all()

        # A câmera usa trigger externo: a própria sequência de medição produz
        # o pulso ``camera_on``. Não acrescente aqui uma espera fixa de 400 ms.
        # Além de não ser necessária para a captura (retrieveBuffer já espera
        # o frame disparado), ela reduz drasticamente a cadência do laser.

        transpiled_circuit = pass_manager.run(circuit)
        _circuitos_transpilados[chave] = transpiled_circuit
    circuit_time = nv_backend.run(transpiled_circuit, typePhoto)

    return circuit_time
