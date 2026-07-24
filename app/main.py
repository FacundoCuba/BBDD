# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session, joinedload
from typing import List, Dict, Any
from datetime import date
import app.database as db_sql
from app.database import get_db
import app.models as schemas
from app.models import EstadoDeterminacionEnum, EstadoMuestraEnum, EstadoServicioEnum

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
    Evalúa el estado de una determinación en base a si posee fecha u ubicación cargada.
    - sin fecha u ubicación = PLANIFICADA
    - con fecha u ubicación = PROCESADA
    """
    if det.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value:
        return

    nombre = det.nombre_determinacion
    esta_procesada = False

    if nombre == "extraccion_adn":
        esta_procesada = det.extraccion_adn and det.extraccion_adn.fecha_extraccion_adn is not None

    elif nombre == "analisis_fragmento":
        esta_procesada = det.analisis_fragmento and det.analisis_fragmento.fecha_analisis_fragmento is not None

    elif nombre == "cuantificacion":
        esta_procesada = det.cuantificacion and det.cuantificacion.fecha_cuantificacion is not None

    elif nombre.startswith("libreria_tanda_"):
        esta_procesada = det.libreria and det.libreria.fecha_libreria is not None

    elif nombre.startswith("secuenciacion_tanda_"):
        esta_procesada = det.secuenciacion and det.secuenciacion.ubicacion_servidor is not None

    if esta_procesada:
        det.estado_determinacion = EstadoDeterminacionEnum.PROCESADA.value
    else:
        det.estado_determinacion = EstadoDeterminacionEnum.PLANIFICADA.value

    db.flush()
    actualizar_estado_muestra(det.id_muestra, db)

def actualizar_estado_muestra(id_muestra: int, db: Session):
    """
    Controla el estado de una muestra:
    - sin determinaciones = PENDIENTE
    - determinaciones planificadas = EN CURSO
    - determinaciones procesadas y sin fecha de entrega = PROCESADA
    - determinaciones procesadas y con fecha de entrega = ENTREGADA
    """
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra or db_muestra.estado_muestra == EstadoMuestraEnum.ELIMINADA.value:
        return

    det_activas = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
    ).all()

    # 1. Sin determinaciones asignadas
    if len(det_activas) == 0:
        db_muestra.estado_muestra = EstadoMuestraEnum.PENDIENTE.value
    else:
        estados_det = [d.estado_determinacion for d in det_activas]
        todas_procesadas = all(est == EstadoDeterminacionEnum.PROCESADA.value for est in estados_det)

        if todas_procesadas:
            if db_muestra.fecha_entrega is not None:
                db_muestra.estado_muestra = EstadoMuestraEnum.ENTREGADA.value
            else:
                db_muestra.estado_muestra = EstadoMuestraEnum.PROCESADA.value
        else:
            # Tiene determinaciones pero al menos una sigue planificada
            db_muestra.estado_muestra = EstadoMuestraEnum.EN_CURSO.value

    db.flush()
    actualizar_estado_servicio(db_muestra.id_servicio, db)

def actualizar_estado_servicio(id_servicio: int, db: Session):
    """
    Controla el estado general del servicio en cascada:
    - Sin muestras asignadas = ABIERTO
    - Muestras sin det. o con det. planificadas = EN CURSO
    - Muestras con det. procesadas y alguna muestra sin fecha de entrega = PROCESADO
    - Muestras con det. procesadas y TODAS con fecha de entrega = FINALIZADO
    """
    db_servicio = db.query(db_sql.ServicioTable).filter(
        db_sql.ServicioTable.id_servicio == id_servicio
    ).first()
    
    if not db_servicio or db_servicio.estado_servicio == EstadoServicioEnum.CANCELADO.value:
        return

    muestras = db.query(db_sql.MuestraTable).filter(
        db_sql.MuestraTable.id_servicio == id_servicio,
        db_sql.MuestraTable.estado_muestra != EstadoMuestraEnum.ELIMINADO.value
    ).all()

    # 1. Sin muestras asignadas
    if not muestras:
        db_servicio.estado_servicio = EstadoServicioEnum.ABIERTO.value
        return

    estados_muestras = [m.estado_muestra for m in muestras]

    # Todas entregadas -> FINALIZADO
    todas_entregadas = all(est == EstadoMuestraEnum.ENTREGADO.value for est in estados_muestras)
    
    # Todas al menos procesadas (procesadas o entregadas) -> PROCESADO
    todas_procesadas_o_mas = all(est in [EstadoMuestraEnum.PROCESADO.value, EstadoMuestraEnum.ENTREGADO.value] for est in estados_muestras)

    if todas_entregadas:
        db_servicio.estado_servicio = EstadoServicioEnum.FINALIZADO.value
    elif todas_procesadas_o_mas:
        db_servicio.estado_servicio = EstadoServicioEnum.PROCESADO.value
    else:
        # Significa que hay muestras en estado "pendiente" o "en curso"
        db_servicio.estado_servicio = EstadoServicioEnum.EN_CURSO.value

    db.flush()

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

    db.flush()
    actualizar_estado_servicio(nuevo_servicio.id_servicio, db)
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

    datos_actualizar = servicio_update.model_dump(exclude_unset=True)
    campos_anulables = ["id_convenio", "comentario_servicio", "detalle_servicio"]
    
    for key, value in datos_actualizar.items():
        if value is not None:
            setattr(db_servicio, key, value)
        elif key in campos_anulables:
            setattr(db_servicio, key, None)
            
    try:
        db.add(db_servicio)
        db.flush()
        actualizar_estado_servicio(id_servicio, db)
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
    
    try:
        db.flush()
        actualizar_estado_servicio(id_servicio, db)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar muestras batch en la base de datos: {str(e)}")

    return {"message": f"{muestras_creadas} muestras procesadas e insertadas con éxito", "id_servicio": id_servicio}

@app.patch("/servicios/{id_servicio}/muestras-batch", status_code=status.HTTP_200_OK)
def actualizar_muestras_batch(
    id_servicio: int, 
    payload: List[Dict[str, Any]] = Body(...), 
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
    ids_muestras_afectadas = []

    for m_data in payload:
        if not isinstance(m_data, dict):
            continue

        id_muestra = m_data.get("id_muestra")

        if m_data.get("tamano_genoma_amplicon") in ["", None, 0]:
            if id_muestra: 
                m_data.pop("tamano_genoma_amplicon", None)
            else:
                m_data["tamano_genoma_amplicon"] = 1

        if m_data.get("nro_ANLIS") == "":
            m_data["nro_ANLIS"] = None

        if id_muestra:
            # ==========================================
            #  CAMINO EDICIÓN: USAMOS MUESTRAUPDATE
            # ==========================================
            db_muestra = db.query(db_sql.MuestraTable).filter(
                db_sql.MuestraTable.id_muestra == int(id_muestra),
                db_sql.MuestraTable.id_servicio == id_servicio
            ).first()

            if not db_muestra:
                continue
            
            try:
                update_schema = schemas.MuestraUpdate(**m_data)
                datos_actualizar = update_schema.model_dump(exclude_unset=True)
                
                for key, value in datos_actualizar.items():
                    setattr(db_muestra, key, value)
                
                db.add(db_muestra)
                muestras_actualizadas += 1
                ids_muestras_afectadas.append(db_muestra.id_muestra)
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
        for id_m in ids_muestras_afectadas:
            actualizar_estado_muestra(id_m, db)
        actualizar_estado_servicio(id_servicio, db)
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
    """
    Trae en lote todas las muestras activas anidadas a un servicio específico,
    excluyendo las marcadas como eliminadas.
    """
    servicio = db.query(db_sql.ServicioTable).filter(
        db_sql.ServicioTable.id_servicio == id_servicio
    ).first()
    
    if not servicio:
        raise HTTPException(status_code=404, detail=f"El servicio con ID {id_servicio} no existe")

    muestras = db.query(db_sql.MuestraTable).filter(
        db_sql.MuestraTable.id_servicio == id_servicio,
        db_sql.MuestraTable.estado_muestra != schemas.EstadoMuestraEnum.ELIMINADA.value
    ).all()

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

    id_presupuesto_val = payload.get("id_presupuesto")
    id_factura_val = payload.get("id_factura")
    id_comprobante_val = payload.get("id_comprobante_pago")

    nuevo_cobro = db_sql.CobroTable(
        id_servicio=int(id_servicio),
        id_presupuesto=str(id_presupuesto_val).strip() if id_presupuesto_val else None,
        monto=float(payload.get("monto")) if payload.get("monto") else 0.0,
        fecha_cobro=fecha_obj,
        id_factura=str(id_factura_val).strip() if id_factura_val else None,
        comprobante_pago=str(id_comprobante_val).strip() if id_comprobante_val and id_comprobante_val != "" else None,
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

@app.post("/muestras/", response_model=schemas.MuestraResponse)
def crear_muestra_endpoint(
    muestra: schemas.MuestraCreate,
    db: Session = Depends(get_db)
):
    dict_m = muestra.dict()
    if "estado_muestra" not in dict_m or not dict_m["estado_muestra"]:
        dict_m["estado_muestra"] = schemas.EstadoMuestraEnum.PENDIENTE.value

    nueva_muestra = db_sql.MuestraTable(**dict_m)
    db.add(nueva_muestra)
    db.flush()
    actualizar_estado_servicio(nueva_muestra.id_servicio, db)
    db.commit()
    db.refresh(nueva_muestra)
    return nueva_muestra

@app.get("/muestras/", response_model=List[schemas.MuestraResponse])
def obtener_muestras(id_servicio: int, db: Session = Depends(db_sql.get_db)):
    """
    Obtiene las muestras activas asociadas a un servicio especifico,
    haciendo eager loading de sus determinaciones relacionadas.
    """
    return db.query(db_sql.MuestraTable)\
             .options(
                 joinedload(db_sql.MuestraTable.determinaciones).joinedload(db_sql.DeterminacionTable.extraccion_adn),
                 joinedload(db_sql.MuestraTable.determinaciones).joinedload(db_sql.DeterminacionTable.analisis_fragmento),
                 joinedload(db_sql.MuestraTable.determinaciones).joinedload(db_sql.DeterminacionTable.cuantificacion),
                 joinedload(db_sql.MuestraTable.determinaciones).joinedload(db_sql.DeterminacionTable.libreria),
                 joinedload(db_sql.MuestraTable.determinaciones).joinedload(db_sql.DeterminacionTable.secuenciacion)
             )\
             .filter(
                 db_sql.MuestraTable.id_servicio == id_servicio,
                 db_sql.MuestraTable.estado_muestra != schemas.EstadoMuestraEnum.ELIMINADA.value
             )\
             .all()

@app.patch("/muestras/{id_muestra}/estado", response_model=schemas.MuestraResponse)
def cambiar_estado_muestra_endpoint(
    id_muestra: int, 
    estado: schemas.EstadoMuestraEnum, 
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(
        db_sql.MuestraTable.id_muestra == id_muestra
    ).first()
    
    if not db_muestra or db_muestra.estado_muestra == schemas.EstadoMuestraEnum.ELIMINADA.value:
        raise HTTPException(status_code=404, detail="Muestra no encontrada o eliminada")
    
    db_muestra.estado_muestra = estado.value

    # Si se marca como ENTREGADA manualmente y no tiene fecha_entrega, se asigna la fecha actual
    if estado == schemas.EstadoMuestraEnum.ENTREGADA and db_muestra.fecha_entrega is None:
        db_muestra.fecha_entrega = date.today()

    db.flush()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.commit()
    db.refresh(db_muestra)

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

@app.patch("/metadata-clinica/batch")
def modificar_metadata_clinica_batch(
    payload: List[schemas.MetadataClinicaResponse],
    db: Session = Depends(db_sql.get_db)
):
    """
    Modificación parcial y masiva (PATCH) de registros de metadata clínica existentes.
    """
    muestras_modificadas = 0
    
    for item in payload:
        id_m = item.id_muestra

        # Verificar si existe la metadata clínica asociada a esa muestra
        db_metadata = db.query(db_sql.MetadataClinicaTable).filter(db_sql.MetadataClinicaTable.id_muestra == id_m).first()
        
        if not db_metadata:
            raise HTTPException(
                status_code=404, 
                detail=f"No se encontró metadata clínica para la muestra con ID {id_m}. Use POST para el alta inicial."
            )

        # Extraemos solo los campos que el frontend explícitamente envió modificados (o seteados)
        datos_actualizar = item.model_dump(exclude_unset=True)

        # Iterar y actualizar dinámicamente los atributos en el ORM (exceptuando la PK)
        for key, value in datos_actualizar.items():
            if key != "id_muestra":
                setattr(db_metadata, key, value)
        
        muestras_modificadas += 1

    db.commit()
    return {"message": f"Se actualizaron {muestras_modificadas} registros de metadata clínica con éxito."}

# =====================================================================
# --- ENDPOINTS: WORKFLOW DE DETERMINACIONES ---
# =====================================================================

@app.post("/determinaciones/planificacion-batch", status_code=status.HTTP_201_CREATED)
def planificacion_determinaciones_batch(
    payload: List[dict] = Body(...), 
    db: Session = Depends(get_db)
):
    if not payload:
        raise HTTPException(status_code=400, detail="El lote de planificación está vacío.")

    try:
        muestras_a_actualizar = set()

        for item in payload:
            id_muestra = item.get("id_muestra")
            if not id_muestra:
                continue

            db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
            if not db_muestra:
                raise HTTPException(status_code=404, detail=f"La muestra con ID {id_muestra} no existe.")

            muestras_a_actualizar.add(id_muestra)

            # 1. DETERMINACIONES SIMPLES
            det_simples = item.get("determinaciones_simples", [])
            mapeo_nombres = {
                "extracción adn": "extraccion_adn",
                "extraccion_adn": "extraccion_adn",
                "análisis de fragmentos": "analisis_fragmento",
                "analisis_fragmento": "analisis_fragmento",
                "cuantificación": "cuantificacion",
                "cuantificacion": "cuantificacion",
                "secuenciacion_tanda_1": "secuenciacion_tanda_1",
                "secuenciacion_tanda_2": "secuenciacion_tanda_2"
            }
            SIMPLES_PERMITIDAS = ["extraccion_adn", "analisis_fragmento", "cuantificacion", "secuenciacion_tanda_1", "secuenciacion_tanda_2"]

            for det_nombre in det_simples:
                nombre_sucio = str(det_nombre).strip().lower()
                det_nombre_clean = mapeo_nombres.get(nombre_sucio, nombre_sucio)

                if det_nombre_clean not in SIMPLES_PERMITIDAS:
                    continue

                existe_individual = db.query(db_sql.DeterminacionTable).filter(
                    db_sql.DeterminacionTable.id_muestra == id_muestra,
                    db_sql.DeterminacionTable.nombre_determinacion == det_nombre_clean
                ).first()
                
                if existe_individual:
                    if existe_individual.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value:
                        existe_individual.estado_determinacion = EstadoDeterminacionEnum.PLANIFICADA.value
                    continue

                nueva_det = db_sql.DeterminacionTable(
                    id_muestra=id_muestra,
                    nombre_determinacion=det_nombre_clean,
                    estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
                )
                db.add(nueva_det)
                db.flush()

                if det_nombre_clean == "extraccion_adn":
                    db.add(db_sql.ExtraccionADNTable(id_determinacion=nueva_det.id_determinacion))
                elif det_nombre_clean == "analisis_fragmento":
                    db.add(db_sql.AnalisisFragmentoTable(id_determinacion=nueva_det.id_determinacion))
                elif det_nombre_clean == "cuantificacion":
                    db.add(db_sql.CuantificacionTable(id_determinacion=nueva_det.id_determinacion))
                elif det_nombre_clean.startswith("secuenciacion_tanda_"):
                    db.add(db_sql.SecuenciacionTable(id_determinacion=nueva_det.id_determinacion, id_corrida=None))

            # 2. LIBRERÍAS
            librerias = item.get("librerias_secuenciaciones", [])
            for lib in librerias:
                if not isinstance(lib, dict):
                    continue
                
                orden_lib = lib.get("orden", 1)
                tech_form = str(lib.get("tecnologia", "")).lower().strip()
                kit_usuario = lib.get("kit")
                
                if tech_form in ["", "no aplica", "no_aplica", "null", "undefined"]:
                    continue

                nombre_det_lib = f"libreria_tanda_{orden_lib}"
                
                existe_tanda = db.query(db_sql.DeterminacionTable).filter(
                    db_sql.DeterminacionTable.id_muestra == id_muestra,
                    db_sql.DeterminacionTable.nombre_determinacion == nombre_det_lib
                ).first()

                if existe_tanda:
                    if existe_tanda.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value:
                        existe_tanda.estado_determinacion = EstadoDeterminacionEnum.PLANIFICADA.value
                    continue

                nueva_det_lib = db_sql.DeterminacionTable(
                    id_muestra=id_muestra,
                    nombre_determinacion=nombre_det_lib,
                    estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
                )
                db.add(nueva_det_lib)
                db.flush()

                db_lib = db_sql.LibreriaTable(
                    id_determinacion=nueva_det_lib.id_determinacion,
                    kit=kit_usuario.strip() if kit_usuario else "Pendiente",
                    tecnologia=tech_form
                )
                db.add(db_lib)

        # Recalcular muestras (y servicios asociados en cascada)
        for id_m in muestras_a_actualizar:
            actualizar_estado_muestra(id_m, db)

        db.commit()
        return {"message": "Planificación masiva inicializada con éxito."}

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Fallo de persistencia en la base de datos: {str(e)}"
        )

@app.patch("/determinaciones/planificacion-batch", status_code=status.HTTP_200_OK)
def actualizar_determinaciones_batch(
    payload: List[dict] = Body(...),
    db: Session = Depends(get_db)
):
    if not payload:
        raise HTTPException(status_code=400, detail="El lote de actualización está vacío.")

    try:
        for item in payload:
            id_muestra = item.get("id_muestra")
            if not id_muestra:
                continue

            db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
            if not db_muestra:
                raise HTTPException(status_code=404, detail=f"La muestra con ID {id_muestra} no existe.")

            # =================================================================
            # 1. SINCRONIZACIÓN DE DETERMINACIONES SIMPLES Y SECUENCIACIONES
            # =================================================================
            det_simples_payload = item.get("determinaciones_simples", [])
            det_simples_payload_clean = [str(x).strip().lower() for x in det_simples_payload]
            
            simples_posibles = [
                "extraccion_adn", "analisis_fragmento", "cuantificacion",
                "secuenciacion_tanda_1", "secuenciacion_tanda_2"
            ]

            for det_nombre in simples_posibles:
                existe_det = db.query(db_sql.DeterminacionTable).filter(
                    db_sql.DeterminacionTable.id_muestra == id_muestra,
                    db_sql.DeterminacionTable.nombre_determinacion == det_nombre
                ).first()

                if det_nombre in det_simples_payload_clean:
                    if not existe_det:
                        existe_det = db_sql.DeterminacionTable(
                            id_muestra=id_muestra,
                            nombre_determinacion=det_nombre,
                            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
                        )
                        db.add(existe_det)
                        db.flush()
                        
                        if det_nombre == "extraccion_adn":
                            db.add(db_sql.ExtraccionADNTable(id_determinacion=existe_det.id_determinacion))
                        elif det_nombre == "analisis_fragmento":
                            db.add(db_sql.AnalisisFragmentoTable(id_determinacion=existe_det.id_determinacion))
                        elif det_nombre == "cuantificacion":
                            db.add(db_sql.CuantificacionTable(id_determinacion=existe_det.id_determinacion))
                        elif det_nombre.startswith("secuenciacion_tanda_"):
                            db.add(db_sql.SecuenciacionTable(id_determinacion=existe_det.id_determinacion, id_corrida=None))
                    else:
                        if existe_det.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value:
                            existe_det.estado_determinacion = EstadoDeterminacionEnum.PLANIFICADA.value
                    
                    db.flush()
                    evaluar_y_actualizar_estado_determinacion(existe_det, db)

                else:
                    # Desplanificación / Soft Delete
                    if existe_det and existe_det.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value:
                        existe_det.estado_determinacion = EstadoDeterminacionEnum.ELIMINADA.value
                        db.flush()
                        evaluar_y_actualizar_estado_determinacion(existe_det, db)

            # =================================================================
            # 2. SINCRONIZACIÓN DE LIBRERÍAS
            # =================================================================
            librerias_payload = item.get("librerias_secuenciaciones", [])
            
            nombres_librerias_payload = []
            for lib in librerias_payload:
                if isinstance(lib, dict) and lib.get("orden"):
                    nombres_librerias_payload.append(f"libreria_tanda_{lib.get('orden')}")

            # Soft delete para librerías excluidas
            librerias_db_existentes = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_muestra == id_muestra,
                db_sql.DeterminacionTable.nombre_determinacion.like("libreria_tanda_%")
            ).all()

            for lib_db in librerias_db_existentes:
                if lib_db.nombre_determinacion not in nombres_librerias_payload:
                    if lib_db.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value:
                        lib_db.estado_determinacion = EstadoDeterminacionEnum.ELIMINADA.value
                        db.flush()
                        evaluar_y_actualizar_estado_determinacion(lib_db, db)

            # Actualización / Inserción de Librerías
            for lib in librerias_payload:
                if not isinstance(lib, dict):
                    continue
                    
                orden_lib = lib.get("orden", 1)
                tech_form = str(lib.get("tecnologia", "")).lower().strip()
                kit_usuario = lib.get("kit")

                if tech_form in ["no_aplica", "", "null", "undefined"]:
                    continue

                nombre_det_lib = f"libreria_tanda_{orden_lib}"

                det_lib = db.query(db_sql.DeterminacionTable).filter(
                    db_sql.DeterminacionTable.id_muestra == id_muestra,
                    db_sql.DeterminacionTable.nombre_determinacion == nombre_det_lib
                ).first()

                if not det_lib:
                    det_lib = db_sql.DeterminacionTable(
                        id_muestra=id_muestra,
                        nombre_determinacion=nombre_det_lib,
                        estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
                    )
                    db.add(det_lib)
                    db.flush()
                elif det_lib.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value:
                    det_lib.estado_determinacion = EstadoDeterminacionEnum.PLANIFICADA.value

                db_lib = db.query(db_sql.LibreriaTable).filter(
                    db_sql.LibreriaTable.id_determinacion == det_lib.id_determinacion
                ).first()
                
                if kit_usuario and kit_usuario.strip() != "":
                    if db_lib:
                        db_lib.kit = kit_usuario.strip()
                        db_lib.tecnologia = tech_form
                    else:
                        db_lib = db_sql.LibreriaTable(
                            id_determinacion=det_lib.id_determinacion, 
                            kit=kit_usuario.strip(),
                            tecnologia=tech_form
                        )
                        db.add(db_lib)

                db.flush()
                evaluar_y_actualizar_estado_determinacion(det_lib, db)

            # Recalcular el estado general de la muestra tras procesar todas las determinaciones
            actualizar_estado_muestra(id_muestra, db)

        db.commit()
        return {"message": "Planificación y estados batch actualizados con éxito vía PATCH."}

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Fallo de persistencia en la base de datos al modificar: {str(e)}"
        )

@app.patch("/muestras/{id_muestra}/extraccion_adn/", response_model=schemas.DeterminacionResponse)
def actualizar_extraccion_adn(
    id_muestra: int, 
    data: schemas.ExtraccionADNUpdate, 
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == "extraccion_adn"
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra, 
            nombre_determinacion="extraccion_adn",
            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
        )
        db.add(det_cabecera)
        db.flush()
    
    db_ext = db.query(db_sql.ExtraccionADNTable).filter(
        db_sql.ExtraccionADNTable.id_determinacion == det_cabecera.id_determinacion
    ).first()
    
    if not db_ext:
        db_ext = db_sql.ExtraccionADNTable(id_determinacion=det_cabecera.id_determinacion)
        db.add(db_ext)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_ext, key, value)
        
    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera, db)
    db.commit()
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
    
    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera, db)
    db.commit()
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
        
    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.patch("/muestras/{id_muestra}/libreria/tanda/{orden}/", response_model=schemas.DeterminacionResponse)
def actualizar_libreria_individual(
    id_muestra: int,
    orden: int,
    data: schemas.LibreriaUpdate,
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    nombre_det_lib = f"libreria_tanda_{orden}"

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == nombre_det_lib,
        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
    ).first()

    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra,
            nombre_determinacion=nombre_det_lib,
            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
        )
        db.add(det_cabecera)
        db.flush()

    db_lib = db.query(db_sql.LibreriaTable).filter(
        db_sql.LibreriaTable.id_determinacion == det_cabecera.id_determinacion
    ).first()

    if not db_lib:
        db_lib = db_sql.LibreriaTable(id_determinacion=det_cabecera.id_determinacion)
        db.add(db_lib)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_lib, key, value)

    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.patch("/muestras/{id_muestra}/secuenciacion/tanda/{orden}/", response_model=schemas.DeterminacionResponse)
def actualizar_secuenciacion_individual(
    id_muestra: int,
    orden: int,
    data: schemas.SecuenciacionUpdate,
    db: Session = Depends(db_sql.get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    nombre_puro = f"secuenciacion_tanda_{orden}"
    nombre_con_lib = f"libreria_secuenciacion_tanda_{orden}"
    
    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion.in_([nombre_puro, nombre_con_lib]),
        db_sql.DeterminacionTable.estado_determinacion != "eliminada"
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra, 
            nombre_determinacion=nombre_puro,
            estado_determinacion="planificada"
        )
        db.add(det_cabecera)
        db.flush()

    db_sec = db.query(db_sql.SecuenciacionTable).filter(
        db_sql.SecuenciacionTable.id_determinacion == det_cabecera.id_determinacion
    ).first()
    
    if not db_sec:
        db_sec = db_sql.SecuenciacionTable(id_determinacion=det_cabecera.id_determinacion, id_corrida=None)
        db.add(db_sec)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_sec, key, value)
        
    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.get("/resultados/filtrar", response_model=List[Dict[str, Any]])
def obtener_matriz_determinaciones(id_servicio: int, tipo_interfaz: str, db: Session = Depends(get_db)):
    """
    Filtra determinaciones activas por Servicio y Tipo de interfaz técnica.
    Garantiza la carga del nombre e identificador de la muestra asociada.
    """
    mapeo_tecnologico = {
        "extraccion_adn": (["extraccion_adn"], db_sql.DeterminacionTable.extraccion_adn),
        "analisis_fragmento": (["analisis_fragmento"], db_sql.DeterminacionTable.analisis_fragmento),
        "cuantificacion": (["cuantificacion"], db_sql.DeterminacionTable.cuantificacion),
        "libreria": (["libreria_tanda_1", "libreria_tanda_2"], db_sql.DeterminacionTable.libreria),
        "secuenciacion": (["secuenciacion_tanda_1", "secuenciacion_tanda_2"], db_sql.DeterminacionTable.secuenciacion)
    }
    
    tipo_key = tipo_interfaz.lower().strip()
    config_tecnologia = mapeo_tecnologico.get(tipo_key)
    if not config_tecnologia:
        raise HTTPException(status_code=400, detail="Tipo de interfaz técnica no soportada.")
    nombres_reales, relacion_subtabla = config_tecnologia

    query = (
        db.query(db_sql.DeterminacionTable)
        .options(
            joinedload(db_sql.DeterminacionTable.muestra),
            joinedload(relacion_subtabla)
        )
        .join(db_sql.MuestraTable, db_sql.DeterminacionTable.id_muestra == db_sql.MuestraTable.id_muestra)
        .filter(db_sql.MuestraTable.id_servicio == id_servicio)
        .filter(db_sql.DeterminacionTable.nombre_determinacion.in_(nombres_reales))
        .filter(db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value)
        .all()
    )

    resultados = []
    for det in query:
        sub_data = {}
        if tipo_key == "extraccion_adn" and det.extraccion_adn:
            sub_data = {c.name: getattr(det.extraccion_adn, c.name) for c in det.extraccion_adn.__table__.columns}
        elif tipo_key == "analisis_fragmento" and det.analisis_fragmento:
            sub_data = {c.name: getattr(det.analisis_fragmento, c.name) for c in det.analisis_fragmento.__table__.columns}
        elif tipo_key == "cuantificacion" and det.cuantificacion:
            sub_data = {c.name: getattr(det.cuantificacion, c.name) for c in det.cuantificacion.__table__.columns}
        elif tipo_key == "libreria" and det.libreria:
            sub_data = {c.name: getattr(det.libreria, c.name) for c in det.libreria.__table__.columns}
        elif tipo_key == "secuenciacion" and det.secuenciacion:
            sub_data = {c.name: getattr(det.secuenciacion, c.name) for c in det.secuenciacion.__table__.columns}
        resultados.append({
            "id_determinacion": det.id_determinacion,
            "id_muestra": det.id_muestra,
            "nombre_muestra": det.muestra.nombre_muestra if det.muestra else f"Muestra {det.id_muestra}",
            "nombre_determinacion_real": det.nombre_determinacion,
            "datos_tecnicos": sub_data
        })
        
    return resultados

@app.patch("/resultados/guardar-lote")
def guardar_resultados_lote(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """
    Persiste modificaciones técnicas únicamente de las filas enviadas por el cliente.
    Permite cargas parciales e ignora registros omitidos.
    Actualiza automáticamente en cascada: Determinación -> Muestra -> Servicio.
    """
    tipo_interfaz = payload.get("tipo_interfaz")
    filas = payload.get("filas", [])
    
    tablas_map = {
        "extraccion_adn": db_sql.ExtraccionADNTable,
        "analisis_fragmento": db_sql.AnalisisFragmentoTable,
        "cuantificacion": db_sql.CuantificacionTable,
        "libreria": db_sql.LibreriaTable,
        "secuenciacion": db_sql.SecuenciacionTable
    }
    
    TablaMapeada = tablas_map.get(tipo_interfaz)
    if not TablaMapeada:
        raise HTTPException(status_code=400, detail="Estructura técnica inválida.")
        
    try:
        for fila in filas:
            id_det = int(fila["id_determinacion"])
            valores = fila.get("valores", {})
            valores_limpios = {
                k: v for k, v in valores.items() 
                if v != "" and v is not None and str(v).lower() not in ["null", "undefined"]
            }

            db_det = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_determinacion == id_det
            ).first()
            
            if not db_det:
                continue
                
            db_sub = db.query(TablaMapeada).filter(TablaMapeada.id_determinacion == id_det).first()
            
            if not db_sub:
                if not valores_limpios:
                    continue
                db_sub = TablaMapeada(id_determinacion=id_det)
                db.add(db_sub)
            
            for k, v in valores.items():
                if v == "" or v is None or str(v).lower() in ["null", "undefined"]:
                    setattr(db_sub, k, None)
                else:
                    if k in ["concentracion_ng_ul", "abs_260_280", "abs_260_230"]:
                        setattr(db_sub, k, float(v))
                    elif k == "id_corrida":
                        setattr(db_sub, k, int(v))
                    elif k.startswith("fecha_"):
                        try:
                            setattr(db_sub, k, date.fromisoformat(str(v).strip()))
                        except ValueError:
                            setattr(db_sub, k, None)
                    else:
                        setattr(db_sub, k, str(v).strip())
            
            db.flush()
            db.expire(db_det)
            evaluar_y_actualizar_estado_determinacion(db_det, db)

        db.commit()
        return {"status": "success", "message": f"Se actualizaron {len(filas)} determinaciones analizadas."}
        
    except Exception as e:
        db.rollback()
        print(f"Error crítico en guardar-lote: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en persistencia masiva: {str(e)}")

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
        tipo_cartucho=payload.get("tipo_cartucho"),
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
            tiempo_final_corrida=payload.get("tiempo_final_corrida") if payload.get("tiempo_final_corrida") != "" else None
        )
        db.add(db_nano)
        
    elif id_tecnologia == "illumina":
        db_illu = db_sql.IlluminaTable(
            id_corrida=db_corrida.id_corrida,
            mail_basespace=payload.get("mail_basespace", ""),
            passing_filter=float(payload.get("passing_filter")) if payload.get("passing_filter") else None,
            clustering=float(payload.get("clustering")) if payload.get("clustering") else None,
            q30=float(payload.get("q30")) if payload.get("q30") else None
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
    payload: dict, 
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
    if "tipo_cartucho" in payload: db_corrida.tipo_cartucho = payload["tipo_cartucho"]
    if "yield_data" in payload: db_corrida.yield_data = payload["yield_data"] if payload["yield_data"] != "" else None
    if "comentario_corrida" in payload: db_corrida.comentario_corrida = payload["comentario_corrida"] if payload["comentario_corrida"] != "" else None

    id_tecnologia = db_corrida.id_tecnologia_plataforma.lower().strip()

    # 2. Actualizar Sub-Tablas dependientes según Plataforma (CON VALIDADORES BLINDADOS)
    try:
        if id_tecnologia == "illumina":
            db_illu = db.query(db_sql.IlluminaTable).filter(db_sql.IlluminaTable.id_corrida == id_corrida).first()
            if db_illu:
                if "mail_basespace" in payload: 
                    db_illu.mail_basespace = payload["mail_basespace"]
                
                # Control estricto de nulos antes de float()
                if "passing_filter" in payload: 
                    val = payload["passing_filter"]
                    db_illu.passing_filter = float(val) if val is not None and val != "" else None
                
                if "clustering" in payload: 
                    val = payload["clustering"]
                    db_illu.clustering = float(val) if val is not None and val != "" else None
                
                if "q30" in payload: 
                    val = payload["q30"]
                    db_illu.q30 = float(val) if val is not None and val != "" else None
                
                db.add(db_illu)

        elif id_tecnologia == "nanopore":
            db_nano = db.query(db_sql.NanoporeTable).filter(db_sql.NanoporeTable.id_corrida == id_corrida).first()
            if db_nano:
                if "modo_basecalling" in payload: 
                    db_nano.modo_basecalling = payload["modo_basecalling"]
                
                # Control estricto de nulos antes de int()
                if "cantidad_inicial_poros" in payload: 
                    val = payload["cantidad_inicial_poros"]
                    db_nano.cantidad_inicial_poros = int(val) if val is not None and val != "" else 0
                
                if "tiempo_final_corrida" in payload: 
                    db_nano.tiempo_final_corrida = payload["tiempo_final_corrida"] if payload["tiempo_final_corrida"] != "" else None
                
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