"""
Servidor concurrente para sincronizar datos de GitHub y atender clientes por sockets.

Protocolo (texto plano, UTF-8):

1) El cliente se conecta y envía una línea con el login de GitHub, por ejemplo:
   "octocat"

2) El servidor valida/crea el usuario en la base (si hace falta) y responde
   con un mensaje de estado inicial, que incluye:
     - si el usuario ya existía o es nuevo,
     - cantidad de repositorios y followers guardados,
     - última fecha de sincronización o 'nunca'.

3) Luego el cliente puede enviar comandos:

   - "/repos"
       Sincroniza repos desde la API de GitHub, los guarda en MySQL y responde
       con la cantidad procesada y la última fecha de sincronización.

   - "/followers"
       Sincroniza followers desde la API de GitHub, los guarda en MySQL y responde
       con la cantidad procesada y la última fecha de sincronización.

   - "/repos_local"
       No llama a la API. Devuelve la lista de repositorios guardados en la base
       para el usuario actual, junto con la fecha de última sincronización
       (o 'nunca' si no se ha sincronizado).

   - "/followers_local"
       No llama a la API. Devuelve la lista de followers guardados en la base
       para el usuario actual, junto con la fecha de última sincronización.

   - "adios"
       El servidor responde con "adios" y cierra la conexión con el cliente.

Cualquier otro comando recibe como respuesta:
   "Comando no reconocido"
"""

import socket
import threading
from datetime import datetime

from src.db import get_conn, init_db
from src.services import (
    set_current_user,
    sync_repos,
    sync_followers,
    show_repos,
    show_followers,
    get_user_status,
)

# Dirección y puerto de escucha del servidor
HOST: str = "0.0.0.0"
PORT: int = 5000

active_clients: int = 0
active_clients_lock = threading.Lock()

def build_commands_help() -> str:
    """
    Devolver el texto con la lista de comandos disponibles.
    """
    return (
        "Comandos disponibles:\n"
        "  /repos           -> sincronizar repos desde GitHub y ver los guardados\n"
        "  /followers       -> sincronizar followers desde GitHub y ver los guardados\n"
        "  /repos_local     -> ver repos guardados en la base\n"
        "  /followers_local -> ver followers guardados en la base\n"
        "  /help            -> mostrar esta ayuda\n"
        "  adios            -> cerrar la conexión\n"
    )


def build_prompt() -> str:
    """
    Mensaje estándar que se agrega al final de cada respuesta.
    """
    return (
        "\nIngrese un nuevo comando. "
        "Escriba /help para ver la lista de comandos.\n"
    )


def ensure_db_connection(db_conn):
    """
    Asegurar que la conexión MySQL siga viva.
    Si no está conectada o da error, devuelve una nueva conexión.
    """
    try:
        if db_conn is not None and db_conn.is_connected():
            return db_conn
    except Exception:
        pass

    # Si se llega hasta acá, hay que crear una conexión nueva
    return get_conn()


def fmt_last_sync(dt: datetime | str | None) -> str:
    """
    Formatear una fecha/hora de última sincronización para mostrar al cliente.

    Acepta None y objetos datetime o strings. Si no hay valor, devuelve 'nunca'.
    Devuelve fechas en formato argentino: DD/MM/YYYY HH:MM.
    """
    if not dt:
        return "nunca"

    try:
        if isinstance(dt, str):
            # Permite strings tipo '2025-11-16 12:48:00'
            dt = datetime.fromisoformat(dt)

        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        # Si algo falla, se devuelve la representación string cruda
        return str(dt)


def build_initial_status_message(status: dict) -> str:
    """
    Construir el mensaje de estado inicial para un usuario dado.

    El diccionario de estado proviene de get_user_status(conn, login)
    y debe contener las claves:
        - 'login' (str | None)
        - 'name' (str | None)
        - 'last_sync_repos' (datetime | None)
        - 'last_sync_followers' (datetime | None)
        - 'repos_count' (int)
        - 'followers_count' (int)
    """
    login = status.get("login") or "(desconocido)"
    name = status.get("name") or "(sin nombre)"

    lsr = fmt_last_sync(status.get("last_sync_repos"))
    lsf = fmt_last_sync(status.get("last_sync_followers"))
    repos_count = int(status.get("repos_count", 0))
    followers_count = int(status.get("followers_count", 0))

    line_header = f"Usuario: {login} – {name}\n"

    resumen = (
        f"Repositorios guardados: {repos_count} (última sync: {lsr})\n"
        f"Followers guardados: {followers_count} (última sync: {lsf})\n"
    )

    commands = "\n" + build_commands_help()

    return line_header + resumen + commands


def build_repos_output(
    login: str,
    last_sync_str: str,
    rows,
    synced_count: int | None = None,
) -> str:
    """
    Construir el texto para mostrar repos guardados (y opcionalmente cuántos se sincronizaron).
    """
    header_lines = [
        f"[Repos guardados para {login}]",
        f"Última sincronización de repos: {last_sync_str}",
    ]
    if synced_count is not None:
        header_lines.append(
            f"Repos sincronizados desde GitHub en esta operación: {synced_count}"
        )

    header = "\n".join(header_lines) + "\n\n"

    if not rows:
        body = "No hay repositorios guardados en la base.\n"
    else:
        body_lines: list[str] = [
            "Nombre                            | Lenguaje     | ★ Stars",
            "-" * 60,
        ]
        for name, lang, stars in rows:
            line = f"{name:<32} | {str(lang or '-'): <12} | {stars}"
            body_lines.append(line)
        body = "\n".join(body_lines) + "\n"

    return header + body


def build_followers_output(
    login: str,
    last_sync_str: str,
    rows,
    synced_count: int | None = None,
) -> str:
    """
    Construir el texto para mostrar followers guardados (y opcionalmente cuántos se sincronizaron).
    """
    header_lines = [
        f"[Followers guardados para {login}]",
        f"Última sincronización de followers: {last_sync_str}",
    ]
    if synced_count is not None:
        header_lines.append(
            f"Followers sincronizados desde GitHub en esta operación: {synced_count}"
        )

    header = "\n".join(header_lines) + "\n\n"

    if not rows:
        body = "No hay followers guardados en la base.\n"
    else:
        body_lines: list[str] = [
            "Login                           | URL",
            "-" * 72,
        ]
        for follower_login, url in rows:
            line = f"{follower_login:<30} | {url or '-'}"
            body_lines.append(line)
        body = "\n".join(body_lines) + "\n"

    return header + body



def handle_client(client_sock: socket.socket, client_addr: tuple[str, int]) -> None:
    """
    Atender a un cliente individual.

    Flujo:
        1) Recibe el login de GitHub.
        2) Asegura que el usuario exista en la base (creándolo si hace falta).
        3) Devuelve un mensaje de estado inicial.
        4) Entra en un bucle de comandos hasta recibir 'adios' o cortar conexión.
    """
    print(f"[+] Conexión aceptada desde {client_addr}")

    global active_clients

    db_conn = None
    try:
        # 1) Abrir conexión a la base para este cliente
        db_conn = get_conn()

        # Incrementar contador de clientes activos
        with active_clients_lock:
            active_clients += 1
            print(f"[INFO] Clientes activos: {active_clients}")

        # 2) Loop de login: permitir reintentos si el usuario no es válido
        while True:
            raw_login = client_sock.recv(1024)
            if not raw_login:
                print(f"[-] {client_addr}: conexión cerrada sin enviar login (o durante login).")
                return

            login = raw_login.decode("utf-8").strip()
            print(f"[{client_addr}] Login recibido: {login!r}")

            # Si el cliente quiere salir antes de loguearse
            if login.lower() == "adios":
                client_sock.sendall("adios\n".encode("utf-8"))
                print(f"[+] {client_addr}: conexión cerrada por 'adios' durante login.")
                return

            # 3) Obtener estado actual del usuario
            status = get_user_status(db_conn, login)

            # Si no existe, se intenta crear en la base validando en GitHub
            if not status["exists"]:
                try:
                    set_current_user(db_conn, login)
                    status = get_user_status(db_conn, login)
                except Exception as e:
                    # NO se cierra  la conexión, da la posibilidad de reintentar con otro nombre de usuario
                    error_msg = (
                        "ERROR_LOGIN "
                        f"Error al validar/crear el usuario '{login}' en GitHub: {e}\n"
                        "Probá con otro nombre de usuario.\n"
                    )
                    print(f"[!] {client_addr} {error_msg.strip()}")
                    client_sock.sendall(error_msg.encode("utf-8"))
                    # Se vuelve al inicio del while para recibir OTRO login
                    continue

            # Si se llega acá, el usuario existe y 'status' es válido
            break

        # 4) Enviar mensaje de estado inicial al cliente (ya con login válido)
        initial_msg = build_initial_status_message(status) + "\n" + build_prompt()
        client_sock.sendall(initial_msg.encode("utf-8"))


        # 5) Bucle de comandos
        while True:
            data = client_sock.recv(4096)
            if not data:
                print(f"[-] {client_addr}: cliente cerró la conexión.")
                break

            cmd = data.decode("utf-8").strip()
            print(f"[{client_addr}] Comando recibido: {cmd!r}")

            # Sincronizar repos desde GitHub y mostrarlos
            if cmd == "/repos":
                try:
                    db_conn = ensure_db_connection(db_conn)
                    synced = sync_repos(db_conn, login)
                    status = get_user_status(db_conn, login)
                    lsr = fmt_last_sync(status.get("last_sync_repos"))
                    rows = show_repos(db_conn, login)

                    msg = build_repos_output(login, lsr, rows, synced)
                except Exception as e:
                    msg = f"Error al sincronizar repos para {login}: {e}\n"

                msg += build_prompt()
                client_sock.sendall(msg.encode("utf-8"))

            # Sincronizar followers desde GitHub y mostrarlos
            elif cmd == "/followers":
                try:
                    db_conn = ensure_db_connection(db_conn)
                    synced = sync_followers(db_conn, login)
                    status = get_user_status(db_conn, login)
                    lsf = fmt_last_sync(status.get("last_sync_followers"))
                    rows = show_followers(db_conn, login)

                    msg = build_followers_output(login, lsf, rows, synced)
                except Exception as e:
                    msg = f"Error al sincronizar followers para {login}: {e}\n"

                msg += build_prompt()
                client_sock.sendall(msg.encode("utf-8"))

            # Ver repos guardados en la base
            elif cmd == "/repos_local":
                #status = get_user_status(db_conn, login)
                lsr = fmt_last_sync(status.get("last_sync_repos"))
                rows = show_repos(db_conn, login)

                msg = build_repos_output(login, lsr, rows)
                msg += build_prompt()
                client_sock.sendall(msg.encode("utf-8"))

            # Ver followers guardados en la base
            elif cmd == "/followers_local":
                #status = get_user_status(db_conn, login)
                lsf = fmt_last_sync(status.get("last_sync_followers"))
                rows = show_followers(db_conn, login)

                msg = build_followers_output(login, lsf, rows)
                msg += build_prompt()
                client_sock.sendall(msg.encode("utf-8"))

            # Mostrar ayuda
            elif cmd in ("/help", "help"):
                msg = build_commands_help() + "\n" + build_prompt()
                client_sock.sendall(msg.encode("utf-8"))

            # Cerrar conexión
            elif cmd == "adios":
                client_sock.sendall("adios".encode("utf-8"))
                print(f"[+] {client_addr}: conexión finalizada por comando 'adios'.")
                break

            # Comando no reconocido
            else:
                msg = (
                    "Comando no reconocido.\n"
                    "Usá /help para ver la lista de comandos.\n"
                )
                msg += build_prompt()
                client_sock.sendall(msg.encode("utf-8"))


    except Exception as e:
        print(f"[!] Error manejando cliente {client_addr}: {e}")
    finally:
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass
        try:
            client_sock.close()
        except Exception:
            pass

        print(f"[-] Conexión cerrada con {client_addr}")
        
        with active_clients_lock:
            active_clients -= 1
            print(f"[INFO] Clientes activos: {active_clients}")
        


def main() -> None:
    """
    Punto de entrada del servidor.

    - Inicializa la base de datos (creación de tablas si no existen).
    - Abre un socket de escucha en HOST:PORT.
    - Por cada nuevo cliente, lanza un hilo que ejecuta handle_client().
    """
    # Inicializar DB una sola vez al inicio del servidor
    conn = None
    try:
        conn = get_conn()
        init_db(conn)
        print("Base de datos inicializada correctamente.")
    except Exception as e:
        print(f"[FATAL] No se pudo inicializar la base de datos: {e}")
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # Crear socket de escucha
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, PORT))
        server_sock.listen()
        # poner timeout corto: hace que accept() espere como máximo 1 segundo.
        # Si no entra ningún cliente, lanza socket.timeout; se la captura y se hace continue.
        # Eso deja al intérprete revisar señales, como Ctrl+C.
        server_sock.settimeout(1.0)  # 1 segundo
        print(f"Servidor escuchando en {HOST}:{PORT}...")

        try:
            while True:
                try:
                    client_sock, client_addr = server_sock.accept()
                except socket.timeout:
                    # No llegó ningún cliente en 1 segundo, se vuelve al while.
                    # Esto permite que Ctrl+C se procese entre timeouts.
                    continue
                except KeyboardInterrupt:
                    print("\nServidor interrumpido por el usuario. Cerrando...")
                    break

                thread = threading.Thread(
                    target=handle_client,
                    args=(client_sock, client_addr),
                    daemon=True,
                )
                thread.start()

        except KeyboardInterrupt:
            # Por si la interrupción cae fuera del accept
            print("\nServidor interrumpido por el usuario. Cerrando...")
        except Exception as e:
            print(f"[FATAL] Error en el loop principal del servidor: {e}")


if __name__ == "__main__":
    main()
