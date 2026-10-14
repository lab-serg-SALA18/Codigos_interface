# Painel de Controle do Laboratório NV

Interface única (pygame) para rodar os experimentos do Lab com o NV. Atualmente estão implementados os experimentos de: ODMR, Rabi, T1, Hahn Echo, Ramsey, CPMG
(2/4/8) e ESEEM

## Arquivos

| Arquivo | O que é | Ambiente |
|---|---|---|
| `interface_laboratorio.py` | **A tela principal — é este que você roda.** | Python 3.6 |
| `configuracoes_lab.py` | `Caminhos`, credenciais, presets de cada experimento e aviso no telegram. | 3.6 e 3.11 |
| `analise_automatica.py` | Detecção de `ressonância` (ODMR) e ajustes de `T1`, `T2`, `T2*`, `Rabi` e `ESEEM`. | 3.6 |
| `experimento_sequencia.py` | Roda os `experimentos` pulsados. | 3.6 |
| `odmr_controller.py` | Roda o experimento de varredura de espetro `ODMR` | 3.6 |
| `servidor_manager.py` | Sobe/derruba o `NVserver.py` e escreve o `ParametrosSequencias.py`. | 3.6 |
| `NVserver.py` | Servidor `local` em que os experimentos são rodados. | 3.11 |
| `GeradorSequencias.py`| Especifica as `sequências` de gates para cada experimento| 3.11 |
| `pulsed_backend.py`| Traduz as **quantum gates** em pulsos de `MW` e `LASER`| 3.11 |
|`qiskit_NV_Backend.py`, `custom_gates.py` | Faz o circuito quântico em `Qiskit` | 3.11 |


## 1. Colocar os arquivos no lugar em `configuracoes_lab.py`


Abra o arquivo e confira/ajuste **uma vez só**:

```
PASTA_PROJETO  = r"C:\Users\lab\Desktop\NV\(seu nome)\Codigos"
PASTA_DADOS_BASE = r"C:\Users\lab\Desktop\NV\(seu nome)"
TELEGRAM_CHAT_ID = "123456789"
TELEGRAM_ATIVO_PADRAO = True
```

Aqui você vai criar uma pasta `Codigos` para copiar e colocar todos os .py e também alterar `configuracoes_lab.py` para salvar os arquivos na sua pasta. 

Criei um bot no telegram para avisar quando uma medida termina (porque algumas podem ser bem demoradas). Para configurar ele você só tem que mudar o seu CHAT_ID (você consegue esse ID pelo app). Para desativar as mensagens é só trocar de True para False


## 2. Rodar

Abra o app da câmera `FlyCap2`


Depois, em Python 3.6, abra o arquivo:
```
python interface_laboratorio.py
```

Isso abre o aplicativo. Para fazer os experimentos:
1. Clique no experimento na barra lateral (ODMR, Rabi, T1, HAN, RAM,
   CPMG-2/4/8, ESEEM).
2. Os campos já vêm preenchidos (primeira vez: com os valores padrão de
   `configuracoes_lab.py`; depois disso: com os últimos valores que você
   usou naquele experimento — fica salvo sozinho em
   `ultima_configuracao.json`).
3. Edite o que quiser e clique em **Executar**.
   - ODMR: mede direto.
   - Os outros: a interface escreve o `ParametrosSequencias.py`, sobe o
     `NVserver.py` no Python 3.11 sozinha (o log dele aparece na caixa de
     status), espera as duas portas responderem, e só então começa a medir.
4. Acompanhe o progresso na barra e no log.
5. No final: o valor ajustado (T1/T2/T2*/frequência de Rabi/frequência do
   ESEEM, ou a ressonância de maior contraste no ODMR) aparece grande no
   painel da direita, com uma prévia do gráfico. **Ver gráfico completo**
   abre o PNG salvo em tamanho real.
6. **Parar** cancela a medida em andamento (espera o passo atual terminar
   e desliga a MW/câmera/servidor com segurança).

Os dados e gráficos são salvos em `(seu nome)\<Experimento>\Dados`
e `(seu nome)\<Experimento>\Graficos` com cada experimento (T1, HAN, RAM...) tendo sua própria subpasta;

## 3. Coisas para conferir antes de confiar 100% nos resultados

Antes de rodar uma medida "pra valer":

- **Câmera:** Confira se a câmera está configurada (tutorial completo em camera.ipynb)

- **Ajuste automático** (`analise_automatica.py`): T1/T2/CPMG usam
  exponencial (esticada para T2), Rabi e Ramsey usam cosseno (amortecido
  quando dá certo), ESEEM usa FFT do resíduo. Se o ajuste falhar (sinal
  muito ruidoso, poucos pontos), a interface mostra a mensagem de erro.
  
  ***NÃO CONFIE 100% NOS FITS GERADOS AUTOMATICAMENTE***, eles podem estar imprecisos. Se quiser um resultado realmente confiável use os arquivos `h5f` e faça um fit específico para cada medida.

## 4. Se algo der errado

O log dentro da caixa de status mostra a saída do `NVserver.py` em tempo
real — geralmente já diz o que houve (porta ocupada, caminho errado do
Python 3.11, erro de conexão com a câmera/GPIB). Erros de hardware
continuam aparecendo como no código original (a interface não tenta
"esconder" uma exceção de verdade, só evita que ela derrube a janela).

Erro Fc2error(18,) significa que um fio da câmera desconectou. Ele fica perto do seu pé, é um fiozinho vermelho e preto que estão enrolados


##### Criado por João Pedro Pereira Nunes em 2026