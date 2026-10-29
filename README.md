# 🧬 Sistema de Gestión y Trazabilidad para Laboratorio de Genómica y Bioinformática

Backend desarrollado en **FastAPI** y **SQLAlchemy** diseñado para la gestión integral del ciclo de vida de muestras biológicas, planificación de determinaciones analíticas (extracción, cuantificación, análisis de fragmentos, preparación de librerías NGS y secuenciación), control de corridas y trazabilidad de datos experimentales.

---

## 🚀 Características Principales

* **Trazabilidad Completa de Muestras:** Seguimiento estructurado desde el alta de la muestra en su servicio hasta la ejecución y control de corridas de secuenciación (**Illumina** y **Oxford Nanopore Technologies**).
* **Gestión Modular de Determinaciones:** Control de estados y flujos lógicos parametrizados para:
  * Extracción de ADN / ARN
  * Análisis de Fragmentos
  * Cuantificación
  * Preparación de Librerías (con selección de kits comerciales predefinidos y soporte para kits personalizados)
  * Secuenciación NGS y auto-vinculación correlativa por posición con librerías asignadas
* **Operaciones Masivas y por Lote:** Endpoint `/resultados/guardar-lote` optimizado para actualizaciones de matriz técnica de alto rendimiento en una única transacción.
* **Modelo Relacional Robusto:** Diseñado con FastAPI y Pydantic v2 para garantizar la validación de esquemas, integridad referencial y control estricto de metadatos de laboratorio (LIMS).

---

## 🛠️ Stack Tecnológico

* **Lenguaje:** Python 3.10+
* **Framework Web:** FastAPI
* **ORM & Base de Datos:** SQLAlchemy (compatible con PostgreSQL / SQLite)
* **Validación de Datos:** Pydantic v2 & ConfigDict
* **Servidor ASGI:** Uvicorn

---

## ⚙️ Instalación y Configuración Local

Clonar el repositorio:

```Bash
git clone [https://github.com/FacundoCuba/BBDD.git](https://github.com/FacundoCuba/BBDD.git)
cd BBDD/app
```

Crear y activar un entorno virtual:

```Bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```
Instalar dependencias:

```Bash
pip install fastapi uvicorn sqlalchemy pydantic
```

Ejecutar el servidor de desarrollo:

```Bash
uvicorn main:app --reload
```

La documentación interactiva de la API (Swagger UI) estará disponible automáticamente en: http://127.0.0.1:8000/docs

---

## 📌 Endpoints Destacados
GET /muestras/: Consulta y filtrado de muestras por servicio.

GET /resultados/filtrar: Obtención de matrices dinámicas de estado por interfaz técnica (libreria, secuenciacion, etc.).

PATCH /muestras/{id_muestra}/libreria/: Actualización y clonación de metadatos de librería.

PATCH /resultados/guardar-lote: Persistencia masiva y transaccional de planificaciones de laboratorio.
