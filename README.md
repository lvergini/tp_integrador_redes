# GitHub → MySQL Sync Server

Servidor concurrente y cliente TCP para sincronizar repositorios y seguidores de GitHub en una base de datos MySQL.  
El sistema permite consultar y actualizar datos mediante comandos de texto, utilizando un protocolo simple y robusto basado en líneas.

---

## Características principales

- **Servidor TCP concurrente** (un hilo por cliente).
- **Cliente de consola** para interactuar fácilmente.
- **Sincronización real con la API de GitHub**:
  - Repositorios del usuario
  - Seguidores (followers) del usuario
- **Persistencia en MySQL** (una conexión por cliente, segura para hilos).
- **Protocolo de texto con framing explícito** mediante `<<END_OF_MESSAGE>>`.
- **Reintentos de login sin cerrar la conexión**.
- **Diseño modular y mantenible**:
  - Servidor
  - Sesiones por cliente
  - Base de datos
  - Servicios
  - API de GitHub
- **Paginación real** (maneja miles de repos/followers sin límite fijo).

---

## Estructura del proyecto

```pwsh
tp_integrador_redes/
│
├── client.py
├── server.py
│
├── .env.example
├── requirements.txt
│
└── src/
    ├── db.py # conexión MySQL + inicialización de tablas
    ├── services.py # lógica de lectura/escritura y sincronización
    └── github_api.py # llamadas a la API de GitHub + paginación
```

---

## Requisitos

- Python 3.10+
- MySQL Server
- Token  de GitHub (opcional recomendado)
- Instalación de dependencias:

```pwsh
pip install -r requirements.txt
```

## Configuración del entorno

1. Crear el archivo .env:

```pwsh
cp .env.example .env
```

2. Completar las variables necesarias:

```pwsh
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxx    # opcional pero recomendado
DB_HOST=localhost
DB_PORT=3306
DB_USER=tu_usuario
MYSQL_PASSWORD=tu_password
MYSQL_DB=githubdb
```

## Base de datos
El servidor inicializa automáticamente las tablas necesarias al arrancar.
La conexión se maneja sesión por sesión, garantizando seguridad en entornos concurrentes.

## Ejecución

1. Iniciar el servidor

```pwsh
python server.py
```

El servidor:
- inicializa la base,
- abre el socket en 0.0.0.0:5000,
- acepta múltiples clientes,
- muestra cantidad de clientes activos.

2. Ejecutar el cliente

```pwsh
python client.py
```

Flujo:
1. El cliente se conecta al servidor.
2. Pide el login de GitHub.
3. El servidor valida el usuario (con reintentos si hay error).
4. El cliente entra al bucle de comandos.

## Protocolo de comunicación

### Cliente → Servidor

- Todas las líneas enviadas terminan en \n
Ejemplos:

```pwsh
octocat\n
/repos\n
/followers_local\n
```

### Servidor → Cliente
- Cada mensaje lógico termina con:

```pwsh
\n<<END_OF_MESSAGE>>\n
```

Esto permite que el cliente lea correctamente mensajes largos (tablas, listados, etc.) usando un buffer incremental.

## Comandos disponibles (desde el cliente)

- /repos	        Sincroniza repos desde GitHub y muestra la tabla desde MySQL
- /followers	    Sincroniza followers desde GitHub
- /repos_local	    Muestra repos guardados en base, sin llamar a la API
- /followers_local	Muestra followers guardados en base
- /help	            Lista de comandos
- /adios	        Cierra la sesión

## Instalación rápida

```pwsh
git clone https://github.com/lvergini/tp_integrador_redes.git
cd tp_integrador_redes
python -m venv .venv
source .venv/bin/activate  # o .venv\Scripts\activate en Windows
pip install -r requirements.txt
cp .env.example .env
# editar .env
python server.py
python client.py
```