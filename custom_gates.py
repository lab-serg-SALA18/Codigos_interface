# -*- coding: utf-8 -*-
"""
custom_gates.py — permite criar quantum gates únicas
"""

from qiskit.circuit import Gate
from qiskit.circuit.quantumregister import QuantumRegister
from qiskit import QuantumCircuit


class TimeXGate(Gate):
    def __init__(self, pulse_time):
        super().__init__('time_x', 1, [pulse_time])

    def _define(self):
        q = QuantumRegister(1, 'q')
        qc = QuantumCircuit(q)
        self.definition = qc
