"""
Cliente para el servidor de GitHub -> MySQL usando sockets.

Flujo básico:

1) El cliente se conecta al servidor (HOST, PORT).
2) Pide por teclado el nombre de usuario de GitHub y lo envía.
3) Muestra el mensaje de estado inicial que devuelve el servidor.
4) Entra en un ciclo de comandos, donde el usuario puede escribir:

   - /repos           -> sincronizar repos desde GitHub y guardar en la base
   - /followers       -> sincronizar followers desde GitHub y guardar en la base
   - /repos_local     -> ver repos guardados en la base
   - /followers_local -> ver followers guardados en la base
   - /help o help     -> ver ayuda de comandos
   - /adios           -> cerrar la conexión

5) El cliente muestra la respuesta del servidor para cada comando.
   Cada respuesta del servidor viene delimitada por el marcador
   especial END_MARKER, que el cliente usa para saber dónde termina.
"""

import socket

HOST: str = "127.0.0.1"  # IP del servidor (localhost por defecto)
PORT: int = 5000         # Puerto del servidor

END_MARKER: str = "\n<<END_OF_MESSAGE>>\n"


class GitHubClient:
    """
    Cliente de consola para conectarse al servidor GitHub -> MySQL.

    Se encarga de:
      - abrir el socket hacia el servidor,
      - manejar el flujo de login, incluyendo reintentos cuando el servidor
        responde con mensajes que comienzan con 'ERROR_LOGIN',
      - enviar comandos escritos por el usuario,
      - recibir y mostrar las respuestas del servidor,
      - cerrar la conexión de forma ordenada.
    """

    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        """
        Inicializar el cliente con la dirección del servidor.

        Parámetros:
            host (str): dirección IP o nombre del host del servidor.
            port (int): puerto TCP del servidor.
        """
        self.host = host
        self.port = port
        self._recv_buffer: str = ""

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _prompt_login(self) -> str:
        """
        Pedir al usuario el login de GitHub por consola.

        Retorna:
            str: nombre de usuario de GitHub (sin espacios en los extremos).
        """
        while True:
            login = input(
                "Ingresá tu usuario de GitHub (o '/adios' para salir): "
            ).strip()
            if login:
                return login
            print("El login no puede estar vacío. Probá de nuevo.")

    def _send_text(self, sock: socket.socket, text: str) -> None:
        """
        Enviar texto al servidor codificado en UTF-8 usando sendall.

        Parámetros:
            sock: socket ya conectado al servidor.
            text (str): texto a enviar.
        """
        sock.sendall(text.encode("utf-8"))

    def _recv_until_marker(
        self,
        sock: socket.socket,
        marker: str = END_MARKER,
        bufsize: int = 4096,
    ) -> str | None:
        """
        Este método asume que el servidor siempre termina cada mensaje
        lógico con END_MARKER.

        Retorna:
            str: texto recibido sin el marcador final.
            None: si el servidor cerró la conexión sin enviar nada.
        """
        while True:
            # Revisa si el marcador está dentro del buffer
            if marker in self._recv_buffer:
                texto, _, rest = self._recv_buffer.partition(marker)
                self._recv_buffer = rest
                return texto

            # Leer más datos del socket
            chunk = sock.recv(bufsize)
            if not chunk:
                # El servidor cerró la conexión
                if self._recv_buffer:
                    texto = self._recv_buffer
                    self._recv_buffer = ""
                    return texto
                return None

            # Acumular en el buffer
            self._recv_buffer += chunk.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Fase de login
    # ------------------------------------------------------------------

    def _login_loop(self, sock: socket.socket) -> bool:
        """
        Manejar el flujo de login con el servidor.

        Paso a paso:
          - pide un nombre de usuario por consola;
          - si el usuario escribe '/adios', envía ese texto al servidor
            y termina el cliente;
          - envía el login al servidor y espera una respuesta;
          - si la respuesta comienza con 'ERROR_LOGIN', muestra el mensaje
            de error y permite reintentar con otro usuario;
          - si la respuesta no comienza con 'ERROR_LOGIN', se asume que es
            el estado inicial y se imprime.

        Parámetros:
            sock: socket ya conectado al servidor.

        Retorna:
            bool: True si se obtuvo un login válido y se recibió el estado
                  inicial; False si el usuario decidió salir con '/adios' o
                  si el servidor cortó la conexión durante el login.
        """
        while True:
            login = self._prompt_login() # pide a usuario por consola el login

            # Opción de salir sin llegar a loguearse
            if login.lower() == "/adios":
                print("Cerrando conexión...")
                self._send_text(sock, "/adios\n")
                return False

            # Enviar login al servidor
            self._send_text(sock, login + "\n")

            # Esperar respuesta (ERROR_LOGIN o estado inicial),
            # que  viene delimitada por END_MARKER
            texto = self._recv_until_marker(sock)
            if texto is None:
                print("El servidor cerró la conexión sin enviar mensaje inicial.")
                return False

            # Manejo de error de login (usuario inválido en GitHub, etc.)
            if texto.startswith("ERROR_LOGIN"):
                print("\n=== Error de login ===")
                print(texto)
                # Se vuelve al while: se pide otro login sobre la misma conexión
                continue

            # Si no empieza con ERROR_LOGIN, se asume que es el estado inicial
            print("\n=== Estado inicial ===")
            print(texto)
            return True

    # ------------------------------------------------------------------
    # Bucle de comandos
    # ------------------------------------------------------------------

    def _command_loop(self, sock: socket.socket) -> None:
        """
        Bucle principal de comandos una vez realizado el login.

        El usuario escribe comandos por consola y el cliente:
          - envía el comando al servidor,
          - espera la respuesta,
          - la muestra en pantalla,
          - sale del bucle si el comando fue '/adios' o si el servidor
            cierra la conexión.
        """
        while True:
            comando = input("> ").strip()
            if not comando:
                # Ignora líneas vacías
                continue

            self._send_text(sock, comando + "\n")

            respuesta = self._recv_until_marker(sock)
            if respuesta is None:
                print("El servidor cerró la conexión.")
                break

            print("\n=== Respuesta del servidor ===")
            print(respuesta)

            if comando == "/adios":
                # El servidor ya respondió "adios" y cerrará su lado
                break

    # ------------------------------------------------------------------
    # Ejecución completa del cliente
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Ejecutar el flujo completo del cliente:

          - conectar al servidor,
          - realizar la fase de login (con reintentos si el servidor
            devuelve ERROR_LOGIN),
          - ejecutar el bucle de comandos hasta '/adios' o corte de conexión.
        """
        print(f"Conectando al servidor en {self.host}:{self.port}...")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.host, self.port))
                print("Conexión establecida.\n")

                # 1) Fase de login
                if not self._login_loop(sock):
                    # El usuario pidió salir o el servidor cortó
                    return

                # 2) Bucle de comandos
                self._command_loop(sock)

        except ConnectionRefusedError:
            print(
                "No se pudo conectar al servidor.\n"
                "¿Está corriendo server.py en el puerto correcto?"
            )
        except KeyboardInterrupt:
            print("\nCliente interrumpido por el usuario.")
        except Exception as e:
            print(f"Error inesperado en el cliente: {e}")


def main() -> None:
    """
    Punto de entrada del cliente.

    Crea una instancia de GitHubClient y ejecuta su flujo principal.
    """
    client = GitHubClient(HOST, PORT)
    client.run()


if __name__ == "__main__":
    main()
