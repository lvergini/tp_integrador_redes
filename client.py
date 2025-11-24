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
   - adios            -> cerrar la conexión

5) El cliente muestra la respuesta del servidor para cada comando.
"""

import socket

HOST: str = "127.0.0.1"  # IP del servidor (localhost por defecto)
PORT: int = 5000         # Puerto del servidor (debe coincidir con server.py)


def pedir_login() -> str:
    """
    Pedir al usuario el login de GitHub por consola.

    Retorna:
        str: nombre de usuario de GitHub (sin espacios en los extremos).
    """
    while True:
        login = input("Ingresá tu usuario de GitHub (o 'adios' para salir): ").strip()
        if login:
            return login
        print("El login no puede estar vacío. Probá de nuevo.")


def main() -> None:
    """
    Punto de entrada del cliente.

    - Se conecta al servidor.
    - Envía el login de GitHub.
    - Muestra el mensaje inicial de estado.
    - Permite enviar comandos hasta escribir 'adios' o perder la conexión.
    """
    print(f"Conectando al servidor en {HOST}:{PORT}...")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((HOST, PORT))
            print("Conexión establecida.\n")

            # 1) Loop de login: permitir reintentos si el servidor responde ERROR_LOGIN
            while True:
                login = pedir_login()
                if login.lower() == "adios":
                    print("Cerrando conexión...")
                    sock.sendall("adios\n".encode("utf-8"))
                    return  # Salimos del cliente
                
                sock.sendall((login + "\n").encode("utf-8"))

                data = sock.recv(8192)
                if not data:
                    print("El servidor cerró la conexión sin enviar mensaje inicial.")
                    return

                texto = data.decode("utf-8")

                if texto.startswith("ERROR_LOGIN"):
                    print("\n=== Error de login ===")
                    print(texto)
                    #print("Intentá con otro usuario.\n")
                    # se vuelve al while: se pide otro login sobre la misma conexión
                    continue

                # Si no empieza con ERROR_LOGIN, se asume  que es el estado inicial
                print("\n=== Estado inicial ===")
                print(texto)
                break

            # 3) Loop de comandos
            print("Escribí un comando (por ejemplo: /repos, /repos_local, adios).")

            while True:
                comando = input("> ").strip()
                if not comando:
                    continue

                sock.sendall((comando + "\n").encode("utf-8"))

                data = sock.recv(8192)
                if not data:
                    print("El servidor cerró la conexión.")
                    break

                respuesta = data.decode("utf-8")
                print("\n=== Respuesta del servidor ===")
                print(respuesta)

                if comando == "adios":
                    # El servidor ya respondió "adios" y cerrará su lado
                    break

    except ConnectionRefusedError:
        print("No se pudo conectar al servidor.\n¿Está corriendo server.py en el puerto correcto?")
    except KeyboardInterrupt:
        print("\nCliente interrumpido por el usuario.")
    except Exception as e:
        print(f"Error inesperado en el cliente: {e}")


if __name__ == "__main__":
    main()
