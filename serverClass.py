"""
Servidor concurrente para sincronizar datos de GitHub y atender clientes por sockets.

Protocolo (texto plano, UTF-8):

1) El cliente se conecta y envía una línea con el login de GitHub, por ejemplo:
   "octocat"

2) El servidor valida/crea el usuario en la base (si hace falta) y responde
   con un mensaje de estado inicial, que incluye:
     - login y nombre del usuario,
     - cantidad de repositorios y followers guardados,
     - fechas de última sincronización de repos y followers (o 'nunca'),
     - listado de comandos disponibles,
     - mensaje de prompt para ingresar un nuevo comando.

   Si el usuario no existe en GitHub o hay error al validarlo, el servidor
   responde con una línea que comienza con:

       ERROR_LOGIN ...

   y mantiene la conexión abierta para que el cliente pueda enviar otro login.

   Si el cliente envía 'adios' durante esta fase de login, el servidor responde
   con 'adios' y cierra la conexión.

3) Luego el cliente puede enviar comandos:

   - "/repos"
       Sincroniza repositorios desde la API de GitHub, los guarda en MySQL y
       responde con:
         * fecha de última sincronización de repos,
         * cantidad sincronizada en esta operación,
         * tabla con nombre, lenguaje y cantidad de estrellas de los repos
           guardados en la base.

   - "/followers"
       Sincroniza followers desde la API de GitHub, los guarda en MySQL y
       responde con:
         * fecha de última sincronización de followers,
         * cantidad sincronizada en esta operación,
         * tabla con login y URL de los followers guardados en la base.

   - "/repos_local"
       No llama a la API. Devuelve:
         * fecha de última sincronización de repos,
         * tabla con los repositorios guardados en la base.

   - "/followers_local"
       No llama a la API. Devuelve:
         * fecha de última sincronización de followers,
         * tabla con los followers guardados en la base.

   - "/help" o "help"
       Muestra la lista de comandos disponibles con una breve descripción.

   - "adios"
       El servidor responde con "adios" y cierra la conexión con el cliente.

   Después de cada comando (salvo cuando se cierra la conexión), el servidor
   agrega un mensaje de prompt:

       "Ingrese un nuevo comando. Escriba /help para ver la lista de comandos."

4) Cualquier otro comando recibe como respuesta:
   "Comando no reconocido.\nUsá /help para ver la lista de comandos.\n"
   seguido del prompt estándar.
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

# Contador global de clientes activos (protegido por lock para uso entre hilos)
active_clients: int = 0
active_clients_lock = threading.Lock()


class ClientSession:
    """
    Representa la sesión de un cliente conectado al servidor.

    Encapsula:
      - el socket del cliente,
      - la dirección remota,
      - la conexión a la base de datos,
      - el login actual,
      - el estado resumido del usuario (status).

    Responsable de:
      - manejar el ciclo de vida de la conexión,
      - resolver el login (con reintentos si es inválido),
      - procesar los comandos recibidos,
      - construir y enviar las respuestas.
    """

    def __init__(self, client_sock: socket.socket, client_addr: tuple[str, int]) -> None:
        self.client_sock = client_sock
        self.client_addr = client_addr
        self.db_conn = None
        self.login: str | None = None
        self.status: dict | None = None
        self._recv_buffer: str = ""


    # -----------------------------------------------------------
    # Métodos auxiliares internos (helpers)
    # -----------------------------------------------------------

    def _send_text(self, text: str) -> None:
        """
        Enviar texto al cliente codificado en UTF-8 usando sendall.

        Garantiza que todo el contenido se envíe o se lance una excepción.
        """
        self.client_sock.sendall(text.encode("utf-8"))

    def _ensure_db_connection(self) -> None:
        """
        Asegurar que la conexión MySQL siga viva.

        Si la conexión actual existe y está conectada, se deja como está.
        Si no, se crea una nueva conexión y se asigna a self.db_conn.
        """
        try:
            if self.db_conn is not None and self.db_conn.is_connected():
                return
        except Exception:
            # Si is_connected() falla, se fuerza creación de una nueva conexión
            pass

        self.db_conn = get_conn()

    def _fmt_last_sync(self, dt: datetime | str | None) -> str:
        """
        Formatear una fecha/hora de última sincronización para mostrar al cliente.

        Acepta None, datetime o string ISO. Si no hay valor, devuelve 'nunca'.
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

    def _build_commands_help(self) -> str:
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

    def _build_prompt(self) -> str:
        """
        Construir el mensaje estándar de prompt que se agrega al final
        de la mayoría de las respuestas.
        """
        return (
            "\nIngrese un nuevo comando. "
            "Escriba /help para ver la lista de comandos.\n"
        )

    def _build_initial_status_message(self, status: dict) -> str:
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

        lsr = self._fmt_last_sync(status.get("last_sync_repos"))
        lsf = self._fmt_last_sync(status.get("last_sync_followers"))
        repos_count = int(status.get("repos_count", 0))
        followers_count = int(status.get("followers_count", 0))

        line_header = f"Usuario: {login} – {name}\n"

        resumen = (
            f"Repositorios guardados: {repos_count} (última sync: {lsr})\n"
            f"Followers guardados: {followers_count} (última sync: {lsf})\n"
        )

        commands = "\n" + self._build_commands_help()

        return line_header + resumen + commands

    def _build_repos_output(
        self,
        login: str,
        last_sync_str: str,
        rows,
        synced_count: int | None = None,
    ) -> str:
        """
        Construir el texto para mostrar repos guardados y, opcionalmente,
        cuántos se sincronizaron en la última operación.
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

    def _build_followers_output(
        self,
        login: str,
        last_sync_str: str,
        rows,
        synced_count: int | None = None,
    ) -> str:
        """
        Construir el texto para mostrar followers guardados y, opcionalmente,
        cuántos se sincronizaron en la última operación.
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

    def _recv_line(self) -> str | None:
        """
        Leer una línea terminada en '\n' desde el socket.

        Devuelve:
            str: línea sin el '\n' final.
            None: si el cliente cerró la conexión antes de completar una línea.
        """
        while True:
            # Si ya existe al menos una línea en el buffer
            if "\n" in self._recv_buffer:
                line, _, rest = self._recv_buffer.partition("\n")
                self._recv_buffer = rest
                return line  # ya es str

            # Si se necesitan leer más datos del socket
            chunk = self.client_sock.recv(4096)
            if not chunk:
                # El cliente cerró la conexión
                if self._recv_buffer:
                    # Devolver lo que quede, aunque no tenga '\n'
                    line = self._recv_buffer
                    self._recv_buffer = ""
                    return line
                return None

            # Se decodifica una sola vez acá
            self._recv_buffer += chunk.decode("utf-8", errors="replace")
            

    def _run_sync_command(
        self,
        *,
        kind: str,
        status_field: str,
        sync_func,
        show_func,
        output_builder,
    ) -> None:
        """
        Helper genérico para comandos que:
          - sincronizan algo desde GitHub,
          - actualizan el estado del usuario,
          - leen datos desde la base,
          - construyen una salida y le agregan el prompt.
        """
        assert self.login is not None
        self._ensure_db_connection()

        try:
            synced = sync_func(self.db_conn, self.login)
            # Actualizar estado del usuario
            self.status = get_user_status(self.db_conn, self.login)
            last_sync_str = self._fmt_last_sync(self.status.get(status_field))
            rows = show_func(self.db_conn, self.login)

            msg = output_builder(self.login, last_sync_str, rows, synced)
        except Exception as e:
            msg = f"Error al sincronizar {kind} para {self.login}: {e}\n"

        msg += self._build_prompt()
        self._send_text(msg)

    def _run_local_command(
        self,
        *,
        status_field: str,
        show_func,
        output_builder,
    ) -> None:
        """
        Helper para comandos 'locales' que:
          - NO sincronizan con GitHub,
          - leen datos de la base,
          - usan self.status para la fecha de última sync,
          - construyen una salida y agregan el prompt.
        """
        assert self.login is not None
        self._ensure_db_connection()

        if self.status is None:
            # En el flujo actual no debería pasar, pero lo dejamos defensivo
            self.status = get_user_status(self.db_conn, self.login)

        last_sync_str = self._fmt_last_sync(self.status.get(status_field))
        rows = show_func(self.db_conn, self.login)

        msg = output_builder(self.login, last_sync_str, rows)
        msg += self._build_prompt()
        self._send_text(msg)

    # -----------------------------------------------------------
    # Fase de login
    # -----------------------------------------------------------

    def _login_loop(self) -> bool:
        """
        Manejar la fase de login del cliente.

        - Recibe el login de GitHub.
        - Si recibe 'adios', responde 'adios' y termina la sesión.
        - Valida/crea el usuario en la base con ayuda de services.
        - En caso de error (por ejemplo, usuario inexistente en GitHub),
          envía un mensaje que comienza con 'ERROR_LOGIN ...' y permite
          reintentar otro login.

        Devuelve:
            True  si se obtuvo un login válido y se inicializó self.status.
            False si el cliente pidió 'adios' durante el login.
        """
        while True:
            raw_login = self._recv_line()
            if raw_login is None:
                print(
                    f"[-] {self.client_addr}: conexión cerrada sin enviar login "
                    "(o durante login)."
                )
                return False

            login = raw_login.strip()
            print(f"[{self.client_addr}] Login recibido: {login!r}")

            # El cliente puede salir antes de loguearse
            if login.lower() == "adios":
                self._send_text("adios\n")
                print(f"[+] {self.client_addr}: conexión cerrada por 'adios' durante login.")
                return False

            # Obtener estado actual del usuario
            status = get_user_status(self.db_conn, login)

            # Si no existe, se intenta crear en la base validando en GitHub
            if not status["exists"]:
                try:
                    set_current_user(self.db_conn, login)
                    status = get_user_status(self.db_conn, login)
                except Exception as e:
                    # NO se cierra la conexión: se da la posibilidad de reintentar
                    error_msg = (
                        "ERROR_LOGIN "
                        f"Error al validar/crear el usuario '{login}' en GitHub: {e}\n"
                        "Probá con otro nombre de usuario.\n"
                    )
                    print(f"[!] {self.client_addr} {error_msg.strip()}")
                    self._send_text(error_msg)
                    # Vuelve al inicio del while para recibir OTRO login
                    continue

            # Si se llega acá, el usuario existe y 'status' es válido
            self.login = login
            self.status = status
            return True

    # -----------------------------------------------------------
    # Comandos
    # -----------------------------------------------------------
    def _cmd_repos(self) -> None:
        """
        Comando /repos:
        Sincroniza repos desde GitHub, los guarda en MySQL y muestra
        el listado actualizado de repos de la base.
        """
        self._run_sync_command(
            kind="repos",
            status_field="last_sync_repos",
            sync_func=sync_repos,
            show_func=show_repos,
            output_builder=self._build_repos_output,
        )

    def _cmd_followers(self) -> None:
        """
        Comando /followers:
        Sincroniza followers desde GitHub, los guarda en MySQL y muestra
        el listado actualizado de followers de la base.
        """
        self._run_sync_command(
            kind="followers",
            status_field="last_sync_followers",
            sync_func=sync_followers,
            show_func=show_followers,
            output_builder=self._build_followers_output,
        )

    def _cmd_repos_local(self) -> None:
        """
        Comando /repos_local:
        No sincroniza. Lee y muestra los repos guardados en la base
        para el usuario actual.
        """
        self._run_local_command(
            status_field="last_sync_repos",
            show_func=show_repos,
            output_builder=self._build_repos_output,
        )

    def _cmd_followers_local(self) -> None:
        """
        Comando /followers_local:
        No sincroniza. Lee y muestra los followers guardados en la base
        para el usuario actual.
        """
        self._run_local_command(
            status_field="last_sync_followers",
            show_func=show_followers,
            output_builder=self._build_followers_output,
        )

    def _cmd_help(self) -> None:
        """
        Comando /help (o help):
        Muestra la lista de comandos disponibles.
        """
        msg = self._build_commands_help() + "\n" + self._build_prompt()
        self._send_text(msg)

    # -----------------------------------------------------------
    # Ciclo de vida completo de la sesión
    # -----------------------------------------------------------

    def run(self) -> None:
        """
        Método principal de la sesión.

        - Abre conexión a la base de datos.
        - Incrementa el contador global de clientes activos.
        - Ejecuta el login con reintentos.
        - Envía el estado inicial.
        - Entra en el bucle de comandos hasta que el cliente envía 'adios'
          o corta la conexión.
        - Cierra recursos y decrementa el contador de clientes activos.
        """

        global active_clients

        print(f"[+] Conexión aceptada desde {self.client_addr}")

        try:
            # Abrir conexión a la base para este cliente
            self.db_conn = get_conn()

            # Incrementar contador de clientes activos (thread-safe)
            with active_clients_lock:
                active_clients += 1
                print(f"[INFO] Clientes activos: {active_clients}")

            # Fase de login (permite reintentos si el usuario no es válido)
            if not self._login_loop():
                return

            # Enviar mensaje de estado inicial al cliente
            initial_msg = (
                self._build_initial_status_message(self.status)
                + "\n"
                + self._build_prompt()
            )
            self._send_text(initial_msg)

            # Bucle de comandos
            while True:
                line = self._recv_line()
                if line is None:
                    print(f"[-] {self.client_addr}: cliente cerró la conexión.")
                    break

                cmd = line.strip()
                print(f"[{self.client_addr}] Comando recibido: {cmd!r}")

                if cmd == "/repos":
                    self._cmd_repos()
                elif cmd == "/followers":
                    self._cmd_followers()
                elif cmd == "/repos_local":
                    self._cmd_repos_local()
                elif cmd == "/followers_local":
                    self._cmd_followers_local()
                elif cmd in ("/help", "help"):
                    self._cmd_help()
                elif cmd == "adios":
                    self._send_text("adios")
                    print(f"[+] {self.client_addr}: conexión finalizada por comando 'adios'.")
                    break
                else:
                    msg = (
                        "Comando no reconocido.\n"
                        "Usá /help para ver la lista de comandos.\n"
                    )
                    msg += self._build_prompt()
                    self._send_text(msg)

        except Exception as e:
            print(f"[!] Error manejando cliente {self.client_addr}: {e}")
        finally:
            # Cerrar conexión a la base
            if self.db_conn is not None:
                try:
                    self.db_conn.close()
                except Exception:
                    pass

            # Cerrar socket del cliente
            try:
                self.client_sock.close()
            except Exception:
                pass

            print(f"[-] Conexión cerrada con {self.client_addr}")

            # Decrementar contador de clientes activos
            with active_clients_lock:
                active_clients -= 1
                print(f"[INFO] Clientes activos: {active_clients}")


class GitHubServer:
    """
    Servidor TCP concurrente para sincronizar datos de GitHub y atender clientes.

    Responsabilidades:
      - Inicializar la base de datos (crear tablas si no existen).
      - Abrir un socket de escucha en HOST:PORT.
      - Aceptar conexiones entrantes.
      - Crear un hilo por cliente, ejecutando ClientSession.run().
      - Manejar el cierre ordenado ante KeyboardInterrupt (Ctrl+C).
    """

    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self.host = host
        self.port = port

    def _init_database(self) -> None:
        """
        Inicializar la base de datos una sola vez al inicio del servidor.
        Crea las tablas requeridas si no existen.
        """
        conn = None
        try:
            conn = get_conn()
            init_db(conn)
            print("Base de datos inicializada correctamente.")
        except Exception as e:
            print(f"[FATAL] No se pudo inicializar la base de datos: {e}")
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def serve_forever(self) -> None:
        """
        Iniciar el servidor y atender clientes en forma concurrente.

        Usa un socket TCP, con:
          - SO_REUSEADDR para permitir reutilizar el puerto,
          - timeout corto en accept() para que Ctrl+C (KeyboardInterrupt)
            se procese con rapidez.
        """
        # Inicializar la DB antes de empezar a escuchar
        try:
            self._init_database()
        except Exception:
            # Si falla la inicialización de la DB, no se arranca el servidor
            return

        # Crear socket de escucha
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            server_sock.listen()
            # Timeout corto para que Ctrl+C se procese entre timeouts
            server_sock.settimeout(1.0)  # 1 segundo
            print(f"Servidor escuchando en {self.host}:{self.port}...")

            try:
                while True:
                    try:
                        client_sock, client_addr = server_sock.accept()
                    except socket.timeout:
                        # No llegó ningún cliente en 1 segundo; se vuelve al while.
                        # Esto permite que KeyboardInterrupt se procese entre timeouts.
                        continue
                    except KeyboardInterrupt:
                        print("\nServidor interrumpido por el usuario. Cerrando...")
                        break

                    # Crear y lanzar la sesión en un hilo daemon
                    session = ClientSession(client_sock, client_addr)
                    thread = threading.Thread(
                        target=session.run,
                        daemon=True,
                    )
                    thread.start()

            except KeyboardInterrupt:
                # Por si la interrupción cae fuera del accept()
                print("\nServidor interrumpido por el usuario. Cerrando...")
            except Exception as e:
                print(f"[FATAL] Error en el loop principal del servidor: {e}")

        print("Servidor apagado.")


def main() -> None:
    """
    Punto de entrada del servidor.

    Crea una instancia de GitHubServer y arranca el loop principal
    de atención a clientes.
    """
    server = GitHubServer(HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
