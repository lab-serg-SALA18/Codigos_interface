"""
Gerado automaticamente pela interface do laboratório em 17/10/2026 18:45:40.
NAO EDITE ESTE ARQUIVO A MAO -- a proxima medida feita pela interface
vai sobrescreve-lo. Para mudar os valores padrao de cada experimento,
edite configuracoes_lab.py.
"""
from spinapi import ns, us, ms, s

SEQUENCY = 'RAM'

FREQUENCY = 3007.0
TIME_ARRAY = range(0, 1000, 50)   # unidade: ns
MW_POWER = 5.0
IMAGES_AVERAGE = 5

LASER_DELAY = 350.0*ns
PI_PULSE_TIME = 112.0*ns
MEASURE_LOOPS = 50
LASER_TIME = 600.0*us

INFOS = f"{FREQUENCY}mhz_{MW_POWER}dbm_{IMAGES_AVERAGE}averages_{TIME_ARRAY[0]}_{TIME_ARRAY[-1]}_{int(LASER_TIME)}ns_{MEASURE_LOOPS}loops_{SEQUENCY}"
