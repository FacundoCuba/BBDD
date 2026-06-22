# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any
from datetime import date
import app.database as db_sql
from app.database import get_db
import app.models as schemas

app = FastAPI(
    title="CNGB API",
    description="Sistema de gestión de CNGB.",
    version="1.3.0"
)

db_sql.Base.metadata.create_all(bind=db_sql.engine)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Hello World"}

# =====================================================================
# --- FUNCIONES AUXILIARES: LÓGICA DE TRANSICIÓN DE ESTADOS ---
# =====================================================================

def evaluar_y_actualizar_estado_determinacion(det: db_sql.DeterminacionTable, db: Session):
    """
    Chequea los campos específicos de cada subtabla técnica para mover
    el estado de una determinación de 'planificada' a 'completada'.
    Si el estado ya es 'eliminada', no lo altera a menos que vuelva a activarse.
    """
    if det.estado_determinacion == "eliminada":
        return

    nombre = det.nombre_determinacion

    if nombre == "extraccion_adn":
        if det.extraccion_adn and det.extraccion_adn.fecha_extraccion_adn is not None:
            det.estado_determinacion = "completada"
        else:
            det.estado_determinacion = "planificada"

    elif nombre == "analisis_fragmento":
        if det.analisis_fragmento and det.analisis_fragmento.fecha_analisis_fragmento is not None:
            det.estado_determinacion = "completada"
        else:
            det.estado_determinacion = "planificada"

    elif nombre == "cuantificacion":
        if det.cuantificacion and det.cuantificacion.fecha_cuantificacion is not None:
            det.estado_determinacion = "completada"
        else:
            det.estado_determinacion = "planificada"

    elif nombre.startswith("libreria_secuenciacion_tanda_"):
        muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == det.id_muestra).first()
        # Si la fecha_entrega ahora vive en MuestraTable:
        if muestra and muestra.fecha_entrega is not None:
            det.estado_determinacion = "completada"
        else:
            det.estado_determinacion = "planificada"


def actualizar_estado_muestra(id_muestra: int, db: Session, forzar_cambio: Optional[str] = None):
    """
    Controla el ciclo de vida de una muestra basado en sus determinaciones y fechas.
    """
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        return

    if forzar_cambio == "eliminado":
        db_muestra.estado_muestra = "eliminado"
        actualizar_estado_servicio(db_muestra.id_servicio, db)
        return

    if db_muestra.metadata_clinica and db_muestra.metadata_clinica.fecha_informe is not None:
        db_muestra.estado_muestra = "entregado"
        actualizar_estado_servicio(db_muestra.id_servicio, db)
        return

    det_activas = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.estado_determinacion != "eliminada"
    ).all()

    if len(det_activas) > 0:
        db_muestra.estado_muestra = "procesando"
    else:
        db_muestra.estado_muestra = "pendiente"

    # db.commit() <- ELIMINADO
    actualizar_estado_servicio(db_muestra.id_servicio, db)


def actualizar_estado_servicio(id_servicio: int, db: Session):
    db_servicio = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not db_servicio or db_servicio.estado_servicio == "cancelado":
        return

    muestras = db.query(db_sql.MuestraTable).filter(
        db_sql.MuestraTable.id_servicio == id_servicio,
        db_sql.MuestraTable.estado_muestra != "eliminado"
    ).all()

    if not muestras:
        db_servicio.estado_servicio = "abierto"
        # db.commit() <- ELIMINADO
        return

    estados = [m.estado_muestra for m in muestras]

    if all(est == "entregado" for est in estados):
        db_servicio.estado_servicio = "finalizado"
    elif any(est in ["procesando", "entregado"] for est in estados):
        db_servicio.estado_servicio = "en curso"
    else:
        db_servicio.estado_servicio = "abierto"

# =====================================================================
# --- ENDPOINTS: USUARIOS ---
# =====================================================================

@app.post("/usuarios/", response_model=schemas.UsuarioResponse, status_code=status.HTTP_201_CREATED)
def crear_usuario(usuario: schemas.UsuarioCreate, db: Session = Depends(db_sql.get_db)):
    db_usuario = db.query(db_sql.UsuarioTable).filter(db_sql.UsuarioTable.mail == usuario.mail).first()
    if db_usuario:
        raise HTTPException(status_code=400, detail="El email ya se encuentra registrado.")
    
    nuevo_usuario = db_sql.UsuarioTable(**usuario.model_dump())
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)
    return nuevo_usuario

@app.get("/usuarios/", response_model=List[schemas.UsuarioResponse])
def listar_usuarios(db: Session = Depends(db_sql.get_db)):
    return db.query(db_sql.UsuarioTable).all()

@app.get("/usuarios/{id_usuario}", response_model=schemas.UsuarioResponse)
def obtener_usuario(id_usuario: int, db: Session = Depends(db_sql.get_db)):
    usuario = db.query(db_sql.UsuarioTable).filter(db_sql.UsuarioTable.id_usuario == id_usuario).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return usuario

@app.patch("/usuarios/{id_usuario}", response_model=schemas.UsuarioResponse)
def actualizar_usuario(
    id_usuario: int, 
    usuario_update: schemas.UsuarioUpdate,  # O schemas.UsuarioCreate si usas el mismo esquema con campos opcionales
    db: Session = Depends(db_sql.get_db)
):
    """
    Actualiza parcialmente un usuario existente (PATCH)
    """
    db_usuario = db.query(db_sql.UsuarioTable).filter(db_sql.UsuarioTable.id_usuario == id_usuario).first()
    if not db_usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    datos_actualizar = usuario_update.model_dump(exclude_unset=True)
    
    for key, value in datos_actualizar.items():
        setattr(db_usuario, key, value)
        
    db.commit()
    db.refresh(db_usuario)
    return db_usuario

# =====================================================================
# --- ENDPOINTS: CONVENIOS ---
# =====================================================================

@app.post("/convenios/", response_model=schemas.ConvenioResponse, status_code=status.HTTP_201_CREATED)
def crear_convenio(convenio: schemas.ConvenioCreate, db: Session = Depends(db_sql.get_db)):
    nuevo_convenio = db_sql.ConvenioTable(**convenio.model_dump())
    db.add(nuevo_convenio)
    db.commit()
    db.refresh(nuevo_convenio)
    return nuevo_convenio

@app.get("/convenios/", response_model=List[schemas.ConvenioResponse])
def listar_convenios(db: Session = Depends(db_sql.get_db)):
    return db.query(db_sql.ConvenioTable).all()

@app.patch("/convenios/{id_convenio}", response_model=schemas.ConvenioResponse)
def actualizar_convenio(
    id_convenio: int, 
    convenio_update: schemas.ConvenioUpdate, # O el esquema que uses con campos opcionales
    db: Session = Depends(db_sql.get_db)
):
    """
    Actualiza parcialmente un convenio existente (PATCH)
    """
    db_convenio = db.query(db_sql.ConvenioTable).filter(db_sql.ConvenioTable.id_convenio == id_convenio).first()
    if not db_convenio:
        raise HTTPException(status_code=404, detail="Convenio no encontrado")
    
    datos_actualizar = convenio_update.model_dump(exclude_unset=True)
    
    for key, value in datos_actualizar.items():
        setattr(db_convenio, key, value)
        
    db.commit()
    db.refresh(db_convenio)
    return db_convenio

# =====================================================================
# --- ENDPOINTS: SERVICIOS ---
# =====================================================================

@app.post("/servicios/", response_model=schemas.ServicioResponse, status_code=status.HTTP_201_CREATED)
def crear_servicio(servicio: schemas.ServicioCreate, db: Session = Depends(db_sql.get_db)):
    datos_servicio = servicio.model_dump(exclude={'muestras'})
    nuevo_servicio = db_sql.ServicioTable(**datos_servicio)
    db.add(nuevo_servicio)
    db.flush()

    for m_schema in servicio.muestras:
        datos_muestra = m_schema.model_dump()
        nueva_muestra = db_sql.MuestraTable(**datos_muestra, id_servicio=nuevo_servicio.id_servicio)
        db.add(nueva_muestra)

    db.commit()
    db.refresh(nuevo_servicio)
    return nuevo_servicio

@app.get("/servicios/", response_model=List[schemas.ServicioResponse])
def listar_servicios(db: Session = Depends(db_sql.get_db)):
    return db.query(db_sql.ServicioTable).all()

@app.get("/servicios/{id_servicio}", response_model=schemas.ServicioResponse)
def obtener_servicio(id_servicio: int, db: Session = Depends(db_sql.get_db)):
    serv = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not serv:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    return serv

@app.patch("/servicios/{id_servicio}", response_model=schemas.ServicioResponse)
def actualizar_servicio(
    id_servicio: int,
    servicio_update: schemas.ServicioUpdate,
    db: Session = Depends(db_sql.get_db)
):
    """
    Actualiza parcialmente los metadatos de un servicio de forma segura (PATCH)
    Soportando valores nulos explícitos en campos opcionales.
    """
    db_servicio = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not db_servicio:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
        
    # model_dump(exclude_unset=True) nos da SOLO los campos que el JSON envió explícitamente
    datos_actualizar = servicio_update.model_dump(exclude_unset=True)
    
    # Lista de campos que permitimos limpiar textualmente o desvincular (poner en NULL)
    campos_anulables = ["id_convenio", "comentario_servicio", "detalle_servicio"]
    
    for key, value in datos_actualizar.items():
        # Si el valor no es None, lo actualizamos normalmente
        if value is not None:
            setattr(db_servicio, key, value)
        # Si el valor ES None, pero es uno de los campos anulables, permitimos el NULL
        elif key in campos_anulables:
            setattr(db_servicio, key, None)
            
    try:
        db.add(db_servicio)
        db.commit()
        db.refresh(db_servicio)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar en la base de datos: {str(e)}")
        
    return db_servicio

@app.post("/servicios/{id_servicio}/muestras-batch", status_code=status.HTTP_201_CREATED)
def agregar_muestras_batch(
    id_servicio: int, 
    payload: List[dict] = Body(...), 
    db: Session = Depends(db_sql.get_db)
):
    
    servicio = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not servicio:
        raise HTTPException(status_code=404, detail=f"El servicio con ID {id_servicio} no existe.")

    muestras_creadas = 0

    for m_data in payload:
        if not isinstance(m_data, dict):
            continue

        m_data["id_servicio"] = id_servicio

        if "tecnologia_requerida" in m_data and m_data["tecnologia_requerida"]:
            tech = str(m_data["tecnologia_requerida"]).lower()
            if tech == "illumina":
                m_data["tecnologia_requerida"] = "illumina"
            elif tech == "nanopore":
                m_data["tecnologia_requerida"] = "nanopore"

        if "fecha_recepcion" in m_data and (m_data["fecha_recepcion"] == "" or m_data["fecha_recepcion"] is None):
            m_data["fecha_recepcion"] = date.today()
        elif "fecha_recepcion" in m_data:
            try:
                m_data["fecha_recepcion"] = date.fromisoformat(str(m_data["fecha_recepcion"]))
            except ValueError:
                m_data["fecha_recepcion"] = date.today()

        try:
            if m_data.get("tamano_genoma_amplicon") == "" or m_data.get("tamano_genoma_amplicon") is None:
                m_data["tamano_genoma_amplicon"] = 0
            else:
                m_data["tamano_genoma_amplicon"] = int(m_data["tamano_genoma_amplicon"])
                
            if m_data.get("nro_ANLIS") == "":
                m_data["nro_ANLIS"] = None
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Error de formato numérico en columnas: {str(e)}")

        try:
            m_schema = schemas.MuestraCreate(**m_data)
        except Exception as e:
            raise HTTPException(
                status_code=422, 
                detail=f"Error de validación estructural en Pydantic: {str(e)}. Datos recibidos: {m_data}"
            )

        nueva_muestra = db_sql.MuestraTable(**m_schema.model_dump())
        db.add(nueva_muestra)
        muestras_creadas += 1
    
    db.commit()
    return {"message": f"{muestras_creadas} muestras procesadas e insertadas con éxito", "id_servicio": id_servicio}

@app.patch("/servicios/{id_servicio}/muestras-batch", status_code=status.HTTP_200_OK)
def actualizar_muestras_batch(
    id_servicio: int, 
    payload: List[Dict[str, Any]] = Body(...), # Recibimos la lista genérica para abrir los dos caminos
    db: Session = Depends(db_sql.get_db)
):
    """
    Sincroniza en lote las muestras de un servicio usando de forma inteligente
    los esquemas MuestraUpdate y MuestraCreate según la existencia de id_muestra.
    """
    servicio = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not servicio:
        raise HTTPException(status_code=404, detail=f"El servicio con ID {id_servicio} no existe.")

    muestras_creadas = 0
    muestras_actualizadas = 0

    for m_data in payload:
        if not isinstance(m_data, dict):
            continue

        id_muestra = m_data.get("id_muestra")

        # pre-procesamiento básico para vacíos de strings que vienen del front antes de Pydantic
        if m_data.get("tamano_genoma_amplicon") in ["", None, 0]:
            # Si tu validador exige > 0, para Update/Create sacamos el campo si viene vacío/cero
            # o lo dejamos pasar sólo si es una creación válida.
            if id_muestra: 
                m_data.pop("tamano_genoma_amplicon", None) # No actualiza el tamaño si se mandó vacío
            else:
                m_data["tamano_genoma_amplicon"] = 1 # Valor mínimo dummy para pasar tu validación de alta

        if m_data.get("nro_ANLIS") == "":
            m_data["nro_ANLIS"] = None

        if id_muestra:
            # ==========================================
            #  CAMINO CAMBIO: USAMOS MUESTRAUPDATE
            # ==========================================
            db_muestra = db.query(db_sql.MuestraTable).filter(
                db_sql.MuestraTable.id_muestra == int(id_muestra),
                db_sql.MuestraTable.id_servicio == id_servicio
            ).first()

            if not db_muestra:
                continue # O lanzar un 404 si preferís cortar todo
            
            try:
                # Validamos el fragmento que viene usando tu esquema de Update
                update_schema = schemas.MuestraUpdate(**m_data)
                # Extraemos solo lo que el usuario alteró o rellenó explícitamente
                datos_actualizar = update_schema.model_dump(exclude_unset=True)
                
                for key, value in datos_actualizar.items():
                    setattr(db_muestra, key, value)
                
                db.add(db_muestra)
                muestras_actualizadas += 1
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=422, detail=f"Error al validar actualización de muestra {id_muestra}: {str(e)}")

        else:
            # ==========================================
            #  CAMINO ALTA: USAMOS MUESTRACREATE
            # ==========================================
            m_data["id_servicio"] = id_servicio
            
            if "tecnologia_requerida" in m_data and m_data["tecnologia_requerida"]:
                m_data["tecnologia_requerida"] = str(m_data["tecnologia_requerida"]).lower().strip()

            try:
                create_schema = schemas.MuestraCreate(**m_data)
                nueva_muestra = db_sql.MuestraTable(**create_schema.model_dump())
                db.add(nueva_muestra)
                muestras_creadas += 1
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=422, detail=f"Error al validar nueva muestra: {str(e)}")

    try:
        db.flush()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en base de datos al impactar lote: {str(e)}")

    return {
        "message": "Sincronización batch exitosa usando esquemas específicos.",
        "creadas": muestras_creadas,
        "actualizadas": muestras_actualizadas,
        "id_servicio": id_servicio
    }

@app.get("/servicios/{id_servicio}/muestras", response_model=List[schemas.MuestraResponse])
def obtener_muestras_por_servicio(id_servicio: int, db: Session = Depends(db_sql.get_db)):
    """Trae en lote todas las muestras anidadas a un servicio específico."""
    muestras = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_servicio == id_servicio).all()
    return muestras

# =====================================================================
# --- ENDPOINTS: COBROS ---
# =====================================================================

@app.post("/cobros/", response_model=schemas.CobroResponse, status_code=status.HTTP_201_CREATED)
def crear_cobro_directo(payload: dict, db: Session = Depends(db_sql.get_db)):
    id_servicio = payload.get("id_servicio")
    if not id_servicio:
        raise HTTPException(status_code=400, detail="Falta el campo id_servicio.")

    servicio = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not servicio:
        raise HTTPException(status_code=404, detail=f"El servicio {id_servicio} no existe.")

    cobro_existente = db.query(db_sql.CobroTable).filter(db_sql.CobroTable.id_servicio == id_servicio).first()
    if cobro_existente:
        raise HTTPException(status_code=400, detail="Este servicio ya cuenta con un cobro registrado.")

    fecha_str = payload.get("fecha_cobro")
    if fecha_str == "" or fecha_str is None:
        fecha_obj = date.today()
    else:
        fecha_obj = date.fromisoformat(fecha_str)

    nuevo_cobro = db_sql.CobroTable(
        id_servicio=int(id_servicio),
        monto=float(payload.get("monto")) if payload.get("monto") else 0.0,
        fecha_cobro=fecha_obj,
        comentario_cobro=payload.get("comentario_cobro") if payload.get("comentario_cobro") != "" else None
    )
    
    db.add(nuevo_cobro)
    db.commit()
    db.refresh(nuevo_cobro)
    return nuevo_cobro

@app.get("/cobros/", response_model=List[schemas.CobroResponse])
def listar_cobros(db: Session = Depends(db_sql.get_db)):
    """
    Retorna la lista de todos los cobros/facturas registrados
    """
    return db.query(db_sql.CobroTable).all()

@app.patch("/cobros/{id_servicio}", response_model=schemas.CobroResponse)
def actualizar_cobro(
    id_servicio: int,
    cobro_update: schemas.CobroUpdate,
    db: Session = Depends(db_sql.get_db)
):
    """
    Actualiza parcialmente un cobro/facturación existente (PATCH) usando id_servicio como PK.
    Permite limpiar campos opcionales enviando null explícito.
    """
    # CORRECCIÓN: Filtrar por id_servicio ya que id_cobro no existe en el modelo
    db_cobro = db.query(db_sql.CobroTable).filter(db_sql.CobroTable.id_servicio == id_servicio).first()
    if not db_cobro:
        raise HTTPException(status_code=404, detail="Registro de cobro no encontrado para ese servicio")
        
    datos_actualizar = cobro_update.model_dump(exclude_unset=True)
    
    # Campos que se pueden limpiar/vaciar (poner en NULL) en la base de datos
    campos_anulables = ["id_comprobante_pago", "comentario_cobro"]
    
    for key, value in datos_actualizar.items():
        if value is not None:
            setattr(db_cobro, key, value)
        elif key in campos_anulables:
            setattr(db_cobro, key, None)
            
    try:
        db.add(db_cobro)
        db.commit()
        db.refresh(db_cobro)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al persistir en BD: {str(e)}")
        
    return db_cobro

# =====================================================================
# --- ENDPOINTS: MUESTRAS Y METADATA CLÍNICA ---
# =====================================================================

@app.get("/muestras/")
def obtener_muestras(id_servicio: int, db: Session = Depends(db_sql.get_db)):
    # Usamos joinedload para traer la tabla de determinaciones junto con la muestra
    return db.query(db_sql.MuestraTable)\
             .options(joinedload(db_sql.MuestraTable.determinaciones))\
             .filter(db_sql.MuestraTable.id_servicio == id_servicio)\
             .all()

# Cambiamos el nombre de la función del endpoint a 'cambiar_estado_muestra_endpoint'
@app.patch("/muestras/{id_muestra}/estado", response_model=schemas.MuestraResponse)
def cambiar_estado_muestra_endpoint(
    id_muestra: int, 
    estado: schemas.EstadoMuestraEnum, 
    db: Session = Depends(get_db) # Aprovechamos a usar el get_db limpio
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail="Muestra no encontrada")
    
    db_muestra.estado_muestra = estado.value
    db.commit()
    db.refresh(db_muestra)
    
    # Esta llamada ahora sí invocará correctamente a la función auxiliar de arriba
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    return db_muestra

@app.post("/metadata-clinica/batch", status_code=status.HTTP_201_CREATED)
def guardar_metadata_clinica_batch(
    payload: List[schemas.MetadataClinicaCreate], 
    db: Session = Depends(db_sql.get_db)
):
    """
    Upsert masivo de metadatos clínicos vinculados a muestras existentes.
    """
    for item in payload:
        id_m = item.id_muestra

        muestra_existe = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_m).first()
        if not muestra_existe:
            raise HTTPException(
                status_code=404, 
                detail=f"La muestra con ID {id_m} no existe en la base de datos."
            )

        # CORREGIDO: Apunta al nombre correcto del ORM: MetadataClinicaTable
        db_metadata = db.query(db_sql.MetadataClinicaTable).filter(db_sql.MetadataClinicaTable.id_muestra == id_m).first()
        datos_fila = item.model_dump(exclude_unset=True)

        if db_metadata:
            for key, value in datos_fila.items():
                if key != "id_muestra":
                    setattr(db_metadata, key, value)
        else:
            db_metadata = db_sql.MetadataClinicaTable(**item.model_dump())
            db.add(db_metadata)
            
    db.commit()
    return {"message": "Lote de metadatos clínicos procesado con éxito."}

# =====================================================================
# --- ENDPOINTS: WORKFLOW DE DETERMINACIONES ---
# =====================================================================

@app.post("/determinaciones/planificacion-batch", status_code=status.HTTP_201_CREATED)
def planificacion_determinaciones_batch(
    payload: List[dict] = Body(...), 
    db: Session = Depends(get_db)
):
    """
    POST: Crea EXCLUSIVAMENTE determinaciones y bloques técnicos desde cero.
    Inicia los estados en 'planificada' por defecto.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="El lote de planificación está vacío.")

    for item in payload:
        id_muestra = item.get("id_muestra")
        if not id_muestra:
            continue

        db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
        if not db_muestra:
            raise HTTPException(status_code=404, detail=f"La muestra con ID {id_muestra} no existe.")

        existe_planificacion = db.query(db_sql.DeterminacionTable).filter(
            db_sql.DeterminacionTable.id_muestra == id_muestra
        ).first()
        if existe_planificacion:
            raise HTTPException(
                status_code=400, 
                detail=f"La muestra ID {id_muestra} ya tiene determinaciones inicializadas. Use PATCH para editar."
            )

        # --- DETERMINACIONES SIMPLES ---
        det_simples = item.get("determinaciones_simples", [])
        for det_nombre in det_simples:
            nueva_det = db_sql.DeterminacionTable(
                id_muestra=id_muestra,
                nombre_determinacion=det_nombre,
                estado_determinacion="planificada"
            )
            db.add(nueva_det)
            db.flush()

            if det_nombre == "extraccion_adn":
                db.add(db_sql.ExtraccionADNTable(id_determinacion=nueva_det.id_determinacion))
            elif det_nombre == "analisis_fragmento":
                db.add(db_sql.AnalisisFragmentoTable(id_determinacion=nueva_det.id_determinacion))
            elif det_nombre == "cuantificacion":
                db.add(db_sql.CuantificacionTable(id_determinacion=nueva_det.id_determinacion))

        # --- LIBRERÍAS Y SECUENCIACIONES ---
        librerias = item.get("librerias_secuenciaciones", [])
        for lib in librerias:
            if not isinstance(lib, dict):
                continue
                
            orden_lib = lib.get("orden", 1)
            tech_form = str(lib.get("tecnologia", "")).lower().strip()
            
            if tech_form == "no_aplica" or tech_form == "":
                continue

            nueva_det_lib = db_sql.DeterminacionTable(
                id_muestra=id_muestra,
                nombre_determinacion=f"libreria_secuenciacion_tanda_{orden_lib}",
                estado_determinacion="planificada"
            )
            db.add(nueva_det_lib)
            db.flush()

            kit_usuario = lib.get("kit_utilizado")
            db_lib = db_sql.LibreriaTable(
                id_determinacion=nueva_det_lib.id_determinacion,
                kit=kit_usuario.strip() if kit_usuario else "Pendiente"
            )
            db.add(db_lib)

            cartucho_usuario = lib.get("tipo_cartucho")
            db_sec = db_sql.SecuenciacionTable(
                id_determinacion=nueva_det_lib.id_determinacion,
                tipo_cartucho=cartucho_usuario.strip() if cartucho_usuario else "Pendiente"
            )
            db.add(db_sec)
        
        actualizar_estado_muestra(id_muestra, db)

    db.commit()
    return {"message": "Planificación masiva inicializada con éxito."}


@app.patch("/determinaciones/planificacion-batch", status_code=status.HTTP_200_OK)
def actualizar_determinaciones_batch(
    payload: List[dict] = Body(...),
    db: Session = Depends(get_db)
):
    """
    PATCH: Modifica determinaciones existentes y aplica BORRADO LÓGICO ('eliminada')
    si una determinación simple ya no viene tildada en el frontend, o si una tanda
    pasa a tecnología 'no_aplica'.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="El lote de actualización está vacío.")

    for item in payload:
        id_muestra = item.get("id_muestra")
        if not id_muestra:
            continue

        db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
        if not db_muestra:
            raise HTTPException(status_code=404, detail=f"La muestra con ID {id_muestra} no existe.")

        # --- 1. PROCESAR DETERMINACIONES SIMPLES (CON BORRADO LÓGICO) ---
        det_simples_payload = item.get("determinaciones_simples", [])
        determinaciones_posibles = ["extraccion_adn", "analisis_fragmento", "cuantificacion"]

        for det_nombre in determinaciones_posibles:
            existe_det = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_muestra == id_muestra,
                db_sql.DeterminacionTable.nombre_determinacion == det_nombre
            ).first()

            if det_nombre in det_simples_payload:
                # Caso: Viene tildada desde el front
                if not existe_det:
                    # Alta incremental
                    nueva_det = db_sql.DeterminacionTable(
                        id_muestra=id_muestra,
                        nombre_determinacion=det_nombre,
                        estado_determinacion="planificada"
                    )
                    db.add(nueva_det)
                    db.flush()
                    
                    if det_nombre == "extraccion_adn":
                        db.add(db_sql.ExtraccionADNTable(id_determinacion=nueva_det.id_determinacion))
                    elif det_nombre == "analisis_fragmento":
                        db.add(db_sql.AnalisisFragmentoTable(id_determinacion=nueva_det.id_determinacion))
                    elif det_nombre == "cuantificacion":
                        db.add(db_sql.CuantificacionTable(id_determinacion=nueva_det.id_determinacion))
                else:
                    # Si estaba eliminada y la volvieron a tildar, se reactiva y se re-evalúa
                    if existe_det.estado_determinacion == "eliminada":
                        existe_det.estado_determinacion = "planificada"
                    evaluar_y_actualizar_estado_determinacion(existe_det, db)
            else:
                # Caso: NO viene en el payload (El usuario la DESTILDÓ en el frontend) -> BORRADO LÓGICO
                if existe_det:
                    existe_det.estado_determinacion = "eliminada"

        # --- 2. PROCESAR LIBRERÍAS Y SECUENCIACIONES (TANDAS) ---
        librerias = item.get("librerias_secuenciaciones", [])
        for lib in librerias:
            if not isinstance(lib, dict):
                continue
                
            orden_lib = lib.get("orden", 1)
            tech_form = str(lib.get("tecnologia", "")).lower().strip()
            nombre_det_lib = f"libreria_secuenciacion_tanda_{orden_lib}"

            det_lib = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_muestra == id_muestra,
                db_sql.DeterminacionTable.nombre_determinacion == nombre_det_lib
            ).first()

            # Caso: El usuario seleccionó "no_aplica" o vació la tanda -> BORRADO LÓGICO
            if tech_form == "no_aplica" or tech_form == "":
                if det_lib:
                    det_lib.estado_determinacion = "eliminada"
                continue

            # Caso contrario: Es una tanda válida (nueva o a actualizar)
            if not det_lib:
                det_lib = db_sql.DeterminacionTable(
                    id_muestra=id_muestra,
                    nombre_determinacion=nombre_det_lib,
                    estado_determinacion="planificada"
                )
                db.add(det_lib)
                db.flush()
            elif det_lib.estado_determinacion == "eliminada":
                det_lib.estado_determinacion = "planificada"

            # Sincronizar subtabla LibreriaTable
            kit_usuario = lib.get("kit_utilizado")
            db_lib = db.query(db_sql.LibreriaTable).filter(db_sql.LibreriaTable.id_determinacion == det_lib.id_determinacion).first()
            if kit_usuario and kit_usuario.strip() != "":
                if db_lib:
                    db_lib.kit = kit_usuario.strip()
                else:
                    db_lib = db_sql.LibreriaTable(id_determinacion=det_lib.id_determinacion, kit=kit_usuario.strip())
                    db.add(db_lib)

            # Sincronizar subtabla SecuenciacionTable
            cartucho_usuario = lib.get("tipo_cartucho")
            cartucho_valor = cartucho_usuario.strip() if cartucho_usuario and cartucho_usuario.strip() != "" else "Pendiente"
            db_sec = db.query(db_sql.SecuenciacionTable).filter(db_sql.SecuenciacionTable.id_determinacion == det_lib.id_determinacion).first()
            if db_sec:
                db_sec.tipo_cartucho = cartucho_valor
            else:
                db_sec = db_sql.SecuenciacionTable(id_determinacion=det_lib.id_determinacion, tipo_cartucho=cartucho_valor)
                db.add(db_sec)

            # Re-evaluar si con los datos de las subtablas pasa a completada
            evaluar_y_actualizar_estado_determinacion(det_lib, db)

        # Recalcular cascada de estados hacia arriba (Muestra -> Servicio)
        actualizar_estado_muestra(id_muestra, db)

    db.commit()
    return {"message": "Planificación y estados batch actualizados con éxito vía PATCH."}

@app.patch("/muestras/{id_muestra}/extraccion_adn/", response_model=schemas.DeterminacionResponse)
def actualizar_extraccion_adn(
    id_muestra: int, 
    data: schemas.ExtraccionADNUpdate, 
    db: Session = Depends(db_sql.get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == "extraccion_adn"
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(id_muestra=id_muestra, nombre_determinacion="extraccion_adn")
        db.add(det_cabecera)
        db.flush()
    
    db_ext = db.query(db_sql.ExtraccionADNTable).filter(
        db_sql.ExtraccionADNTable.id_determinacion == det_cabecera.id_determinacion
    ).first()
    
    if not db_ext:
        db_ext = db_sql.ExtraccionADNTable(id_determinacion=det_cabecera.id_determinacion)
        db.add(db_ext)

    # Actualización segura basada en los campos reales del esquema enviado
    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_ext, key, value)
        
    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(det_cabecera)
    return det_cabecera


@app.patch("/muestras/{id_muestra}/analisis_fragmento/", response_model=schemas.DeterminacionResponse)
def actualizar_analisis_fragmento(
    id_muestra: int, 
    data: schemas.AnalisisFragmentoUpdate, 
    db: Session = Depends(db_sql.get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail="Muestra no encontrada.")

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == "analisis_fragmento"
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(id_muestra=id_muestra, nombre_determinacion="analisis_fragmento")
        db.add(det_cabecera)
        db.flush()
    
    db_frag = db.query(db_sql.AnalisisFragmentoTable).filter(
        db_sql.AnalisisFragmentoTable.id_determinacion == det_cabecera.id_determinacion
    ).first()
    
    if not db_frag:
        db_frag = db_sql.AnalisisFragmentoTable(id_determinacion=det_cabecera.id_determinacion)
        db.add(db_frag)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_frag, key, value)
        
    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(det_cabecera)
    return det_cabecera


@app.patch("/muestras/{id_muestra}/cuantificacion/", response_model=schemas.DeterminacionResponse)
def actualizar_cuantificacion(
    id_muestra: int, 
    data: schemas.CuantificacionUpdate, 
    db: Session = Depends(db_sql.get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail="Muestra no encontrada.")

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == "cuantificacion"
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(id_muestra=id_muestra, nombre_determinacion="cuantificacion")
        db.add(det_cabecera)
        db.flush()
    
    db_cuanti = db.query(db_sql.CuantificacionTable).filter(
        db_sql.CuantificacionTable.id_determinacion == det_cabecera.id_determinacion
    ).first()
    
    if not db_cuanti:
        db_cuanti = db_sql.CuantificacionTable(id_determinacion=det_cabecera.id_determinacion)
        db.add(db_cuanti)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_cuanti, key, value)
        
    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(det_cabecera)
    return det_cabecera


@app.patch("/muestras/{id_muestra}/tanda/{orden}/", response_model=schemas.DeterminacionResponse)
def actualizar_libreria_secuenciacion_tanda(
    id_muestra: int,
    orden: int,
    payload: Dict[str, Any] = Body(...), 
    db: Session = Depends(db_sql.get_db)
):
    """
    Parchea la tanda técnica correlacionando los diccionarios con las propiedades 
    reales de las tablas (kit y tipo_cartucho). Devuelve el response_model unificado de auditoría.
    """
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail="Muestra no encontrada.")

    nombre_det_lib = f"libreria_secuenciacion_tanda_{orden}"
    
    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == nombre_det_lib
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(id_muestra=id_muestra, nombre_determinacion=nombre_det_lib)
        db.add(det_cabecera)
        db.flush()

    # --- 1. Mapeo y validación de Subtabla Librería ---
    if "kit" in payload and payload["kit"] is not None:
        db_lib = db.query(db_sql.LibreriaTable).filter(db_sql.LibreriaTable.id_determinacion == det_cabecera.id_determinacion).first()
        if db_lib:
            db_lib.kit = str(payload["kit"]).strip()
        else:
            db_lib = db_sql.LibreriaTable(id_determinacion=det_cabecera.id_determinacion, kit=str(payload["kit"]).strip())
            db.add(db_lib)

    # --- 2. Mapeo y validación de Subtabla Secuenciación ---
    if "tipo_cartucho" in payload and payload["tipo_cartucho"] is not None:
        db_sec = db.query(db_sql.SecuenciacionTable).filter(db_sql.SecuenciacionTable.id_determinacion == det_cabecera.id_determinacion).first()
        if db_sec:
            db_sec.tipo_cartucho = str(payload["tipo_cartucho"]).strip()
        else:
            db_sec = db_sql.SecuenciacionTable(id_determinacion=det_cabecera.id_determinacion, tipo_cartucho=str(payload["tipo_cartucho"]).strip())
            db.add(db_sec)

    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(det_cabecera)
    return det_cabecera

# =====================================================================
# --- ENDPOINTS: CORRIDAS ---
# =====================================================================

@app.post("/corridas/", response_model=schemas.CorridaResponse, status_code=status.HTTP_201_CREATED)
def crear_corrida(payload: dict, db: Session = Depends(db_sql.get_db)):
    """
    Endpoint polimórfico para corridas distribuidas (Illumina / Nanopore).
    """
    nombre_corrida = payload.get("nombre_corrida")
    id_tecnologia_raw = payload.get("id_tecnologia_plataforma")
    
    if not nombre_corrida or not id_tecnologia_raw:
        raise HTTPException(status_code=400, detail="Faltan campos mandatorios: nombre o plataforma.")

    id_tecnologia = id_tecnologia_raw.lower().strip()
    fecha_str = payload.get("fecha_corrida")
    fecha_obj = date.fromisoformat(fecha_str) if fecha_str else date.today()
    
    db_corrida = db_sql.CorridaTable(
        nombre_corrida=nombre_corrida.strip(),
        fecha_corrida=fecha_obj,
        id_tecnologia_plataforma=id_tecnologia,
        equipo_corrida=payload.get("equipo_corrida"),
        yield_data=payload.get("yield_data") if payload.get("yield_data") != "" else None,
        comentario_corrida=payload.get("comentario_corrida") if payload.get("comentario_corrida") != "" else None
    )
    
    db.add(db_corrida)
    db.flush()

    if id_tecnologia == "nanopore":
        db_nano = db_sql.NanoporeTable(
            id_corrida=db_corrida.id_corrida,
            modo_basecalling=payload.get("modo_basecalling", "FAS"),
            cantidad_inicial_poros=int(payload.get("cantidad_inicial_poros")) if payload.get("cantidad_inicial_poros") else 0,
            tiempo_final_corrida=payload.get("tiempo_final_corrida") if payload.get("tiempo_final_corrida") != "" else None,
            lote_flowcell=payload.get("lote_flowcell") if payload.get("lote_flowcell") != "" else None
        )
        db.add(db_nano)
        
    elif id_tecnologia == "illumina":
        vto_str = payload.get("vto_cartucho")
        vto_obj = date.fromisoformat(vto_str) if vto_str and vto_str != "" else None

        db_illu = db_sql.IlluminaTable(
            id_corrida=db_corrida.id_corrida,
            # CORREGIDO: En database.py cantidad_ciclos está definido como VARCHAR/String
            cantidad_ciclos=str(payload.get("cantidad_ciclos")) if payload.get("cantidad_ciclos") else "0",
            mail_basespace=payload.get("mail_basespace", ""),
            passing_filter=float(payload.get("passing_filter")) if payload.get("passing_filter") else None,
            clustering=float(payload.get("clustering")) if payload.get("clustering") else None,
            q30=float(payload.get("q30")) if payload.get("q30") else None,
            lote_cartucho=payload.get("lote_cartucho") if payload.get("lote_cartucho") != "" else None,
            vto_cartucho=vto_obj
        )
        db.add(db_illu)

    db.commit()
    db.refresh(db_corrida)
    return db_corrida

@app.get("/corridas/", response_model=List[schemas.CorridaResponse])
def listar_corridas(db: Session = Depends(db_sql.get_db)):
    return db.query(db_sql.CorridaTable).all()

@app.patch("/corridas/{id_corrida}", response_model=schemas.CorridaResponse)
def actualizar_corrida(
    id_corrida: int,
    payload: dict, # Recibimos dict para manejar el parseo polimórfico flexible del JSON anidado
    db: Session = Depends(db_sql.get_db)
):
    """
    Actualiza parcialmente una corrida y su sub-tabla tecnológica asociada (Illumina/Nanopore).
    """
    db_corrida = db.query(db_sql.CorridaTable).filter(db_sql.CorridaTable.id_corrida == id_corrida).first()
    if not db_corrida:
        raise HTTPException(status_code=404, detail="Corrida no encontrada")

    # 1. Actualizar Datos de la Tabla Base Corrida
    if "nombre_corrida" in payload: db_corrida.nombre_corrida = payload["nombre_corrida"].strip()
    if "fecha_corrida" in payload and payload["fecha_corrida"]: 
        db_corrida.fecha_corrida = date.fromisoformat(payload["fecha_corrida"])
    if "equipo_corrida" in payload: db_corrida.equipo_corrida = payload["equipo_corrida"]
    if "yield_data" in payload: db_corrida.yield_data = payload["yield_data"] if payload["yield_data"] != "" else None
    if "comentario_corrida" in payload: db_corrida.comentario_corrida = payload["comentario_corrida"] if payload["comentario_corrida"] != "" else None

    id_tecnologia = db_corrida.id_tecnologia_plataforma.lower().strip()

    # 2. Actualizar Sub-Tablas dependientes según Plataforma
    try:
        if id_tecnologia == "illumina":
            db_illu = db.query(db_sql.IlluminaTable).filter(db_sql.IlluminaTable.id_corrida == id_corrida).first()
            if db_illu:
                if "cantidad_ciclos" in payload: db_illu.cantidad_ciclos = str(payload["cantidad_ciclos"])
                if "mail_basespace" in payload: db_illu.mail_basespace = payload["mail_basespace"]
                if "passing_filter" in payload: db_illu.passing_filter = float(payload["passing_filter"]) if payload["passing_filter"] != "" else None
                if "clustering" in payload: db_illu.clustering = float(payload["clustering"]) if payload["clustering"] != "" else None
                if "q30" in payload: db_illu.q30 = float(payload["q30"]) if payload["q30"] != "" else None
                if "lote_cartucho" in payload: db_illu.lote_cartucho = payload["lote_cartucho"] if payload["lote_cartucho"] != "" else None
                if "vto_cartucho" in payload:
                    db_illu.vto_cartucho = date.fromisoformat(payload["vto_cartucho"]) if payload["vto_cartucho"] else None
                db.add(db_illu)

        elif id_tecnologia == "nanopore":
            db_nano = db.query(db_sql.NanoporeTable).filter(db_sql.NanoporeTable.id_corrida == id_corrida).first()
            if db_nano:
                if "modo_basecalling" in payload: db_nano.modo_basecalling = payload["modo_basecalling"]
                if "cantidad_inicial_poros" in payload: db_nano.cantidad_inicial_poros = int(payload["cantidad_inicial_poros"]) if payload["cantidad_inicial_poros"] else 0
                if "tiempo_final_corrida" in payload: db_nano.tiempo_final_corrida = payload["tiempo_final_corrida"] if payload["tiempo_final_corrida"] != "" else None
                if "lote_flowcell" in payload: db_nano.lote_flowcell = payload["lote_flowcell"] if payload["lote_flowcell"] != "" else None
                db.add(db_nano)

        db.add(db_corrida)
        db.commit()
        db.refresh(db_corrida)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error procesando actualización polimórfica: {str(e)}")

    return db_corrida

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)