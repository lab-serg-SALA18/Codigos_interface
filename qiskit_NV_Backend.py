# -*- coding: utf-8 -*-
"""
qiskit_NV_Backend.py — sem mudanças de lógica em relação ao original.

Roda dentro do NVserver.py (Python 3.11). Define o backend Qiskit
personalizado que representa o centro NV (portas suportadas, tempos de
execução) e a ponte entre o circuito transpilado e translate_circuit_to_pulses.
"""

from qiskit.transpiler import Target, InstructionProperties
from qiskit.circuit.library import XGate, HGate, RXGate, Measure
from qiskit.circuit.delay import Delay
from qiskit.providers import BackendV2, Options
from pulsed_backend import *
from custom_gates import TimeXGate
from qiskit.circuit import Parameter

# Criar o Target
# O Target define as propriedades do backend, como as portas suportadas e suas características.
target = Target(num_qubits=1)

# Adicionar as portas suportadas ao Target
# - XGate: Rotacoes pi
# - HGate: Rotacoes pi/2
# - RXGate: Rotação theta
# - Measure: Operacao de medicao

# Duration é o tempo necessário para tal porta lógica acontecer.
target.add_instruction(
    XGate(),
    properties={(0,): InstructionProperties(duration=90e-9, error=0.01)}
)
target.add_instruction(
    HGate(),
    properties={(0,): InstructionProperties(duration=45e-9, error=0.005)}
)
target.add_instruction(
    RXGate,
    name="rx"
)
target.add_instruction(
    TimeXGate(Parameter("pulse_time")),
    name="time_x"
)
target.add_instruction(
    Delay,
    name="delay"
)
target.add_instruction(
    Measure(),
    properties={(0,): InstructionProperties(duration=1e-1, error=0.02)}
)


# Criar o Backend personalizado
class NVCenterBackend(BackendV2):
    def __init__(self):
        super().__init__(
            name="NV_backend",
            description="Backend personalizado para o NV",
            backend_version="1.0.0",
        )
        self._target = target  # Associar o Target ao backend

    @property
    def target(self):
        return self._target

    @property
    def max_circuits(self):
        return 1  # Número máximo de circuitos que podem ser executados simultaneamente

    @classmethod
    def _default_options(cls):
        return Options(shots=1)

    def run(self, circuit, typePhoto):
        """
        Funcao que conecta o Qiskit ao sistema experimental.
        - Traduz as portas lógicas em pulsos.
        - Envia os pulsos para o hardware.
        - Executa a medicao e retorna o resultado.
        """
        pulse_sequence, circuit_time = translate_circuit_to_pulses(circuit, typePhoto)
        resposta = send_pulses(pulse_sequence)
        return circuit_time
