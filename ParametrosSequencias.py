"""
Gerado automaticamente pela interface do laboratório em 30/09/2026 16:52:03.
NAO EDITE ESTE ARQUIVO A MAO -- a proxima medida feita pela interface
vai sobrescreve-lo. Para mudar os valores padrao de cada experimento,
edite configuracoes_lab.py.
"""
from spinapi import ns, us, ms, s

SEQUENCY = 'RABI'

FREQUENCY = 3007.7
TIME_ARRAY = range(1, 100, 10)   # unidade: ns
MW_POWER = 0.0
IMAGES_AVERAGE = 10

LASER_DELAY = 350.0*ns
PI_PULSE_TIME = 0.0*ns
MEASURE_LOOPS = 250
LASER_TIME = 450.0*us

INFOS = f"{FREQUENCY}mhz_{MW_POWER}dbm_{IMAGES_AVERAGE}averages_{TIME_ARRAY[0]}_{TIME_ARRAY[-1]}_{int(LASER_TIME)}ns_{MEASURE_LOOPS}loops_{SEQUENCY}"
