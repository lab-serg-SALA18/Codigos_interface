# -*- coding: utf-8 -*-
"""
NVserver.py — roda no Python 3.11 (ambiente do Qiskit).
"""

import socket
import json
import time
import threading

from spinapi import *

from pulsed_backend import initialize_spincore
from GeradorSequencias import circuit_sequence


rodando = True


# ====================== SPINAPI SERVER ====================== #

def send_pulses(pulse_sequence):
    """
    Programa e inicia uma sequência na SpinCore.

    Mantém exatamente a lógica usada pelo estado funcional:
      pb_stop()
      pb_reset()
      programação
      pb_start()
    """

    pb_stop()
    pb_reset()

    pb_start_programming(PULSE_PROGRAM)

    try:
        for i, pulse in enumerate(pulse_sequence):
            try:
                pb_inst_pbonly(*pulse[:3],int(max(pulse[3], 15)))

            except Exception as e:
                print(f"[!] Erro no pulso {i}: "f"{pulse} - {e}")

                try:
                    pb_stop_programming()
                except Exception:
                    pass

                return {"status": "erro","msg": f"Falha ao programar pulso {i}",}

        pb_stop_programming()

        pb_start()

        return {"status": "ok","msg": "Sequência executada",}

    except Exception as e:
        try:
            pb_stop_programming()
        except Exception:
            pass

        return {"status": "erro","msg": str(e),}


def parar_spincore_servidor():
    """
    Para o programa atual e libera a placa.

    Chamado no shutdown e no encerramento do processo.
    """

    try:
        pb_stop()
    except Exception:
        pass

    try:
        pb_close()
    except Exception:
        pass


def handle_command_spinapi(cmd):
    global rodando

    if cmd["acao"] == "send_pulses":

        pulse_sequence = cmd["pulse_sequence"]

        t0 = time.time()

        resultado = send_pulses(pulse_sequence)

        t1 = time.time()

        if resultado["status"] != "ok":
            return resultado

        return {"status": "ok","msg": "Sequência executada","tempo": round(t1 - t0,3,),}

    elif cmd["acao"] == "shutdown":

        rodando = False

        parar_spincore_servidor()

        return {"status": "ok","msg": "Servidor encerrando",}

    return {"status": "erro","msg": "Comando desconhecido",}


def spinapi_server():

    server = socket.socket(socket.AF_INET,socket.SOCK_STREAM,)

    server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1,)

    server.settimeout(1.0)

    server.bind(("127.0.0.1",65432,))

    server.listen()

    print("[SpinAPI Server] ""Aguardando comandos em ""127.0.0.1:65432...")

    try:
        while rodando:

            try:
                conn, _ = server.accept()

                with conn:

                    while rodando:

                        try:
                            data = conn.recv(8192)

                            if not data:
                                break

                            comando = json.loads(data.decode())

                            resposta = handle_command_spinapi(comando)

                            conn.sendall(
                                json.dumps(resposta).encode("utf-8"))

                        except socket.timeout:
                            continue

                        except Exception as e:
                            print("[SpinAPI Server] "f"Erro na conexão: {e}")

                            try:
                                resposta = {"status": "erro","msg": str(e),}

                                conn.sendall(json.dumps(resposta).encode("utf-8"))
                            except Exception:
                                break

            except KeyboardInterrupt:
                print("\n[SpinAPI Server] ""Interrompido.")
                break

            except Exception as e:
                if rodando:
                    print("[SpinAPI Server] "f"Erro inesperado: {e}")

    finally:
        try:
            server.close()
        except Exception:
            pass

        print("[SpinAPI Server] ""Finalizado.")


# ====================== QISKIT SERVER ====================== #

def qiskit_server():

    server = socket.socket(socket.AF_INET,socket.SOCK_STREAM,)

    server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1,)

    server.settimeout(1.0)

    server.bind(
        ("127.0.0.1",65433,))

    server.listen()

    print("[Qiskit Server] ""Aguardando comandos em ""127.0.0.1:65433...")

    try:
        while rodando:

            try:
                conn, _ = server.accept()

                with conn:

                    while rodando:

                        try:
                            data = conn.recv(4096)

                            if not data:
                                break

                            comando = json.loads(data.decode())

                            mw_value = int(comando["mw_value"])

                            typePhoto = comando["typePhoto"]

                            if comando["acao"] == ("circuit_sequence"):

                                circuit_time = (circuit_sequence(mw_value,typePhoto,))

                            else:
                                raise ValueError("Ação inválida")

                            resposta = {"status": "ok","circuit_time": circuit_time,}

                        except Exception as e:

                            print("[Qiskit Server] "f"Erro: {e}")

                            resposta = {"status": "erro","msg": str(e),}

                        try:
                            conn.sendall(json.dumps(resposta).encode("utf-8"))
                        except Exception:
                            break

            except socket.timeout:
                continue

            except KeyboardInterrupt:
                print("\n[Qiskit Server] ""Interrompido.")
                break

            except Exception as e:
                if rodando:
                    print("[Qiskit Server] "f"Erro crítico: {e}")

    finally:
        try:
            server.close()
        except Exception:
            pass

        print("[Qiskit Server] ""Finalizado.")


# ====================== MAIN ====================== #

if __name__ == "__main__":

    print("[Sistema] ""Inicializando Spincore...")

    initialize_spincore()

    pb_start()

    t1 = threading.Thread(target=spinapi_server,daemon=True,)

    t2 = threading.Thread(target=qiskit_server,daemon=True,)

    t1.start()
    t2.start()

    print("[Sistema] ""Servidor NV rodando. ""Pressione Ctrl+C para sair.")

    try:

        while rodando:
            time.sleep(1)

    except KeyboardInterrupt:

        print("\n[Sistema] ""Encerrando todos os servidores...")

        rodando = False

    finally:

        parar_spincore_servidor()

        print("[Sistema] ""Servidor NV encerrado.")