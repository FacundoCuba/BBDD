# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload
from typing import List, Dict, Any, Optional
from datetime import date
import app.database as db_sql
from app.database import get_db
import app.models as schemas
from app.models import EstadoDeterminacionEnum, EstadoMuestraEnum, EstadoServicioEnum, EstadoCorridaEnum

app = FastAPI(
    title="CNGB API",
    description="Sistema de gestión de CNGB.",
    version="1.3.0"
)

db_sql.Base.metadata.create_all(bind=db_sql.engine)

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

def evaluar_y_actualizar_estado_determinacion(id_determinacion: int, db: Session):
    """
    Evalúa el estado de una determinación en base a si posee fecha u ubicación cargada.
    - sin fecha u ubicación = PLANIFICADA
    - con fecha u ubicación = PROCESADA
    """
    det = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_determinacion == id_determinacion
    ).first()

    if not det or det.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value:
        return

    nombre = det.nombre_determinacion or ""
    esta_procesada = False

    if nombre == "extraccion_adn":
        esta_procesada = bool(det.extraccion_adn and det.extraccion_adn.fecha_extraccion_adn)

    elif nombre == "analisis_fragmento":
        esta_procesada = bool(det.analisis_fragmento and det.analisis_fragmento.fecha_analisis_fragmento)

    elif nombre == "cuantificacion":
        esta_procesada = bool(det.cuantificacion and det.cuantificacion.fecha_cuantificacion)

    elif nombre.startswith("libreria"):
        esta_procesada = bool(det.libreria and det.libreria.fecha_libreria)

    elif nombre.startswith("secuenciacion"):
        if det.secuenciacion:
            tiene_path = bool(det.secuenciacion.ubicacion_servidor and det.secuenciacion.ubicacion_servidor.strip())
            
            # Si se vinculó a una corrida, también podemos comprobar si la corrida finalizó
            corrida_realizada = (
                det.secuenciacion.corrida and 
                det.secuenciacion.corrida.estado_corrida == EstadoCorridaEnum.REALIZADA.value
            )
            
            # Se considera procesada si tiene ruta en servidor O la corrida asociada ya finalizó
            esta_procesada = tiene_path or corrida_realizada

    # Actualizamos estado según la evaluación
    if esta_procesada:
        det.estado_determinacion = EstadoDeterminacionEnum.PROCESADA.value
    else:
        det.estado_determinacion = EstadoDeterminacionEnum.PLANIFICADA.value

    db.flush()
    
    # Propagación en cascada a la Muestra
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
        db_sql.MuestraTable.estado_muestra != EstadoMuestraEnum.ELIMINADA.value
    ).all()

    # 1. Sin muestras asignadas
    if not muestras:
        db_servicio.estado_servicio = EstadoServicioEnum.ABIERTO.value
        return

    estados_muestras = [m.estado_muestra for m in muestras]

    # Todas entregadas -> FINALIZADO
    todas_entregadas = all(est == EstadoMuestraEnum.ENTREGADA.value for est in estados_muestras)
    
    # Todas al menos procesadas (procesadas o entregadas) -> PROCESADO
    todas_procesadas_o_mas = all(est in [EstadoMuestraEnum.PROCESADA.value, EstadoMuestraEnum.ENTREGADA.value] for est in estados_muestras)

    if todas_entregadas:
        db_servicio.estado_servicio = EstadoServicioEnum.FINALIZADO.value
    elif todas_procesadas_o_mas:
        db_servicio.estado_servicio = EstadoServicioEnum.PROCESADO.value
    else:
        # Significa que hay muestras en estado "pendiente" o "en curso"
        db_servicio.estado_servicio = EstadoServicioEnum.EN_CURSO.value

    db.flush()

def evaluar_y_actualizar_estado_corrida(id_corrida: int, db: Session):
    """
    Evalúa y actualiza el estado de una corrida de secuenciación.
    - Si tiene nombre y no tiene fecha -> PLANIFICADA
    - Si tiene nombre y tiene fecha     -> REALIZADA
    """
    db_corrida = db.query(db_sql.CorridaTable).filter(
        db_sql.CorridaTable.id_corrida == id_corrida
    ).first()

    if not db_corrida:
        return

    if db_corrida.estado_corrida != EstadoCorridaEnum.CANCELADA.value:
        tiene_nombre = bool(db_corrida.nombre_corrida and db_corrida.nombre_corrida.strip())
        tiene_fecha = db_corrida.fecha_corrida is not None

        if tiene_nombre and tiene_fecha:
            db_corrida.estado_corrida = EstadoCorridaEnum.REALIZADA.value
        else:
            db_corrida.estado_corrida = EstadoCorridaEnum.PLANIFICADA.value

    db.flush()

    determinaciones = db.query(db_sql.SecuenciacionTable.id_determinacion).filter(
        db_sql.SecuenciacionTable.id_corrida == id_corrida
    ).all()
    for (id_determinacion,) in determinaciones:
        evaluar_y_actualizar_estado_determinacion(id_determinacion, db)

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
    campos_obligatorios = {"instituto", "abreviacion", "nombre", "apellido", "mail"}
    for campo in campos_obligatorios.intersection(datos_actualizar):
        valor = datos_actualizar[campo]
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            raise HTTPException(status_code=422, detail=f"El campo {campo} es obligatorio.")
        if isinstance(valor, str):
            datos_actualizar[campo] = valor.strip()
    
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
    campos_obligatorios = {"fecha_convenio", "nombre_convenio", "cantidad_muestras_total"}
    for campo in campos_obligatorios.intersection(datos_actualizar):
        valor = datos_actualizar[campo]
        if valor is None or (campo == "nombre_convenio" and not valor.strip()):
            raise HTTPException(status_code=422, detail=f"El campo {campo} es obligatorio.")
    if "nombre_convenio" in datos_actualizar:
        datos_actualizar["nombre_convenio"] = datos_actualizar["nombre_convenio"].strip()
    
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
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="La fecha de recepción tiene un formato inválido.")

        try:
            if m_data.get("tamano_genoma_amplicon") in ("", None):
                m_data["tamano_genoma_amplicon"] = None
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

        if "tamano_genoma_amplicon" in m_data:
            valor_genoma = m_data["tamano_genoma_amplicon"]
            if valor_genoma in ("", None):
                raise HTTPException(status_code=422, detail="El tamaño del genoma debe ser un entero positivo.")
            try:
                m_data["tamano_genoma_amplicon"] = int(valor_genoma)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="El tamaño del genoma debe ser un entero positivo.")
            if m_data["tamano_genoma_amplicon"] <= 0:
                raise HTTPException(status_code=422, detail="El tamaño del genoma debe ser un entero positivo.")
        if "reads_profundidad_requerida" in m_data and (
            m_data["reads_profundidad_requerida"] is None
            or not str(m_data["reads_profundidad_requerida"]).strip()
        ):
            raise HTTPException(status_code=422, detail="La profundidad de lecturas es obligatoria.")

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
                db.rollback()
                raise HTTPException(
                    status_code=404,
                    detail=f"La muestra {id_muestra} no existe en el servicio {id_servicio}."
                )
            
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

@app.delete("/muestras/{id_muestra}", status_code=status.HTTP_200_OK)
def eliminar_muestra(id_muestra: int, db: Session = Depends(db_sql.get_db)):
    muestra = db.query(db_sql.MuestraTable).filter(
        db_sql.MuestraTable.id_muestra == id_muestra
    ).first()
    if not muestra:
        raise HTTPException(status_code=404, detail="Muestra no encontrada.")

    id_servicio = muestra.id_servicio
    try:
        muestra.estado_muestra = EstadoMuestraEnum.ELIMINADA.value
        db.add(muestra)
        actualizar_estado_servicio(id_servicio, db)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al eliminar la muestra: {str(e)}")

    return {"message": f"Muestra {id_muestra} eliminada correctamente."}

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
    try:
        fecha_obj = date.today() if fecha_str == "" or fecha_str is None else date.fromisoformat(fecha_str)
        monto = float(payload.get("monto")) if payload.get("monto") else 0.0
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="La fecha o el monto del cobro tienen un formato inválido.")

    id_presupuesto_val = payload.get("id_presupuesto")
    id_factura_val = payload.get("id_factura")
    comprobante_val = payload.get("comprobante_pago")

    nuevo_cobro = db_sql.CobroTable(
        id_servicio=int(id_servicio),
        id_presupuesto=str(id_presupuesto_val).strip() if id_presupuesto_val else None,
        monto=monto,
        fecha_cobro=fecha_obj,
        id_factura=str(id_factura_val).strip() if id_factura_val else None,
        comprobante_pago=str(comprobante_val).strip() if comprobante_val and comprobante_val != "" else None,
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
    campos_anulables = [
        "id_presupuesto",
        "fecha_cobro",
        "id_factura",
        "comprobante_pago",
        "comentario_cobro",
    ]
    
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
    payload: List[schemas.MetadataClinicaBatchUpdate],
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
        datos_actualizar = item.model_dump(exclude_unset=True, exclude={"id_muestra"})

        for key, value in datos_actualizar.items():
            setattr(db_metadata, key, value)
        
        muestras_modificadas += 1

    db.commit()
    return {"message": f"Se actualizaron {muestras_modificadas} registros de metadata clínica con éxito."}

# =====================================================================
# --- ENDPOINTS: WORKFLOW DE DETERMINACIONES ---
# =====================================================================

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
        db_sql.DeterminacionTable.nombre_determinacion == "extraccion_adn",
        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
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
    evaluar_y_actualizar_estado_determinacion(det_cabecera.id_determinacion, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.patch("/muestras/{id_muestra}/analisis_fragmento/", response_model=schemas.DeterminacionResponse)
def actualizar_analisis_fragmento(
    id_muestra: int, 
    data: schemas.AnalisisFragmentoUpdate, 
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == "analisis_fragmento",
        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra, 
            nombre_determinacion="analisis_fragmento",
            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
        )
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
    evaluar_y_actualizar_estado_determinacion(det_cabecera.id_determinacion, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.patch("/muestras/{id_muestra}/cuantificacion/", response_model=schemas.DeterminacionResponse)
def actualizar_cuantificacion(
    id_muestra: int, 
    data: schemas.CuantificacionUpdate, 
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion == "cuantificacion",
        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra, 
            nombre_determinacion="cuantificacion",
            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
        )
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
    evaluar_y_actualizar_estado_determinacion(det_cabecera.id_determinacion, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.patch("/muestras/{id_muestra}/libreria/", response_model=schemas.DeterminacionResponse)
def actualizar_libreria(
    id_muestra: int,
    data: schemas.LibreriaUpdate,
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")
    
    det_cabecera = db.query(db_sql.DeterminacionTable).filter(
        db_sql.DeterminacionTable.id_muestra == id_muestra,
        db_sql.DeterminacionTable.nombre_determinacion.in_(["libreria"]),
        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
    ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra,
            nombre_determinacion="libreria",
            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
        )
        db.add(det_cabecera)
        db.flush()
    
    db_lib = db.query(db_sql.LibreriaTable).filter(
        db_sql.LibreriaTable.id_determinacion == det_cabecera.id_determinacion
    ).first()

    if not db_lib:
        db_lib = db_sql.LibreriaTable(
            id_determinacion=det_cabecera.id_determinacion,
            id_libreria=det_cabecera.id_determinacion
        )
        db.add(db_lib)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_lib, key, value)

    # GARANTIZAR CLONACIÓN SIEMPRE
    db_lib.id_libreria = det_cabecera.id_determinacion

    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera.id_determinacion, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.patch("/muestras/{id_muestra}/secuenciacion/", response_model=schemas.DeterminacionResponse)
def actualizar_secuenciacion(
    id_muestra: int,
    data: schemas.SecuenciacionUpdate,
    id_determinacion: Optional[int] = None,
    db: Session = Depends(get_db)
):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail=f"Muestra ID {id_muestra} no encontrada.")

    if id_determinacion is not None:
        det_cabecera = db.query(db_sql.DeterminacionTable).filter(
            db_sql.DeterminacionTable.id_determinacion == id_determinacion,
            db_sql.DeterminacionTable.id_muestra == id_muestra,
            db_sql.DeterminacionTable.nombre_determinacion == "secuenciacion",
            db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
        ).first()
        if not det_cabecera:
            raise HTTPException(
                status_code=404,
                detail=f"La determinación {id_determinacion} no existe, no pertenece a la muestra o no es una secuenciación activa."
            )
    else:
        det_cabecera = db.query(db_sql.DeterminacionTable).filter(
            db_sql.DeterminacionTable.id_muestra == id_muestra,
            db_sql.DeterminacionTable.nombre_determinacion == "secuenciacion",
            db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
        ).first()
    
    if not det_cabecera:
        det_cabecera = db_sql.DeterminacionTable(
            id_muestra=id_muestra, 
            nombre_determinacion="secuenciacion",
            estado_determinacion=EstadoDeterminacionEnum.PLANIFICADA.value
        )
        db.add(det_cabecera)
        db.flush()

    db_sec = db.query(db_sql.SecuenciacionTable).filter(
        db_sql.SecuenciacionTable.id_determinacion == det_cabecera.id_determinacion
    ).first()
    
    if not db_sec:
        db_sec = db_sql.SecuenciacionTable(id_determinacion=det_cabecera.id_determinacion)
        db.add(db_sec)

    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(db_sec, key, value)

    # VINCULACIÓN INTELIGENTE Y CORRELATIVA DE LIBRERÍA
    if not db_sec.id_libreria:
        # Traemos todas las librerías activas de esta muestra ordenadas cronológicamente
        librerias_muestra = db.query(db_sql.DeterminacionTable).filter(
            db_sql.DeterminacionTable.id_muestra == id_muestra,
            db_sql.DeterminacionTable.nombre_determinacion.ilike("%libreria%"),
            db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
        ).order_by(db_sql.DeterminacionTable.id_determinacion.asc()).all()

        if librerias_muestra:
            # Buscamos cuántas secuenciaciones van creadas antes que esta
            secuenciaciones_previas = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_muestra == id_muestra,
                db_sql.DeterminacionTable.nombre_determinacion == "secuenciacion",
                db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value,
                db_sql.DeterminacionTable.id_determinacion <= det_cabecera.id_determinacion
            ).order_by(db_sql.DeterminacionTable.id_determinacion.asc()).all()
            
            indice_match = min(len(secuenciaciones_previas) - 1, len(librerias_muestra) - 1)
            indice_match = max(0, indice_match)
            
            db_sec.id_libreria = librerias_muestra[indice_match].id_determinacion

    db.flush()
    evaluar_y_actualizar_estado_determinacion(det_cabecera.id_determinacion, db)
    db.commit()
    db.refresh(det_cabecera)
    return det_cabecera

@app.get("/resultados/filtrar", response_model=List[Dict[str, Any]])
def obtener_matriz_determinaciones(id_servicio: int, tipo_interfaz: str, db: Session = Depends(get_db)):
    mapeo_tecnologico = {
        "extraccion_adn": (["extraccion_adn"], db_sql.DeterminacionTable.extraccion_adn),
        "analisis_fragmento": (["analisis_fragmento"], db_sql.DeterminacionTable.analisis_fragmento),
        "cuantificacion": (["cuantificacion"], db_sql.DeterminacionTable.cuantificacion),
        "libreria": (["libreria"], db_sql.DeterminacionTable.libreria),
        "secuenciacion": (["secuenciacion"], db_sql.DeterminacionTable.secuenciacion)
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
        subtabla = getattr(det, tipo_key, None)
        if subtabla:
            sub_data = {c.name: getattr(subtabla, c.name) for c in subtabla.__table__.columns}

        # SI ES SECUENCIACIÓN: Buscamos y adjuntamos los datos de la librería vinculada para trazabilidad
        info_libreria_asociada = {}
        if tipo_key == "secuenciacion" and subtabla and getattr(subtabla, "id_libreria", None):
            det_lib_rel = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_determinacion == subtabla.id_libreria
            ).first()
            if det_lib_rel and det_lib_rel.libreria:
                info_libreria_asociada = {
                    "nombre_libreria": det_lib_rel.libreria.nombre_libreria,
                    "kit": det_lib_rel.libreria.kit
                }

        resultados.append({
            "id_determinacion": det.id_determinacion,
            "id_muestra": det.id_muestra,
            "nombre_muestra": det.muestra.nombre_muestra if det.muestra else f"Muestra {det.id_muestra}",
            "nombre_determinacion_real": det.nombre_determinacion,
            "datos_tecnicos": sub_data,
            "libreria_asociada": info_libreria_asociada # <-- Se inyecta la info de la librería
        })
        
    return resultados

@app.patch("/resultados/guardar-lote")
def guardar_resultados_lote(payload: Dict[str, Any], db: Session = Depends(get_db)):
    tipo_interfaz = payload.get("tipo_interfaz")
    filas = payload.get("filas", [])
    
    tablas_map = {
        "extraccion_adn": (db_sql.ExtraccionADNTable, "extraccion_adn"),
        "analisis_fragmento": (db_sql.AnalisisFragmentoTable, "analisis_fragmento"),
        "cuantificacion": (db_sql.CuantificacionTable, "cuantificacion"),
        "libreria": (db_sql.LibreriaTable, "libreria"),
        "secuenciacion": (db_sql.SecuenciacionTable, "secuenciacion")
    }
    
    tipo_interfaz = tipo_interfaz.strip().lower() if isinstance(tipo_interfaz, str) else ""
    configuracion = tablas_map.get(tipo_interfaz)
    if not configuracion:
        raise HTTPException(status_code=400, detail="Estructura técnica inválida.")
    TablaMapeada, nombre_determinacion = configuracion
    if not isinstance(filas, list):
        raise HTTPException(status_code=422, detail="El campo filas debe ser una lista.")

    columnas_editables = {
        column.name for column in TablaMapeada.__table__.columns
        if column.name != "id_determinacion"
        and not (tipo_interfaz == "libreria" and column.name == "id_libreria")
    }

    try:
        for fila in filas:
            if not isinstance(fila, dict) or "id_determinacion" not in fila:
                raise HTTPException(status_code=422, detail="Cada fila debe incluir id_determinacion.")
            try:
                id_det = int(fila["id_determinacion"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="id_determinacion debe ser un entero.")

            valores = fila.get("valores", {})
            if not isinstance(valores, dict):
                raise HTTPException(status_code=422, detail="El campo valores debe ser un objeto.")
            campos_desconocidos = set(valores) - columnas_editables
            if campos_desconocidos:
                raise HTTPException(
                    status_code=422,
                    detail=f"Campos no válidos para {tipo_interfaz}: {', '.join(sorted(campos_desconocidos))}."
                )

            db_det = db.query(db_sql.DeterminacionTable).filter(
                db_sql.DeterminacionTable.id_determinacion == id_det
            ).first()
            
            if not db_det:
                raise HTTPException(status_code=404, detail=f"Determinación {id_det} no encontrada.")
            if (
                db_det.nombre_determinacion != nombre_determinacion
                or db_det.estado_determinacion == EstadoDeterminacionEnum.ELIMINADA.value
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"La determinación {id_det} no es una {nombre_determinacion} activa."
                )
                
            db_sub = db.query(TablaMapeada).filter(TablaMapeada.id_determinacion == id_det).first()
            
            if not db_sub:
                valores_limpios = any(
                    value not in ("", None)
                    and str(value).lower() not in ("null", "undefined")
                    for value in valores.values()
                )
                if not valores_limpios:
                    continue
                db_sub = TablaMapeada(id_determinacion=id_det)
                db.add(db_sub)

            for k, v in valores.items():
                if v == "" or v is None or (isinstance(v, str) and v.lower() in ["null", "undefined"]):
                    setattr(db_sub, k, None)
                else:
                    try:
                        if k in ["concentracion_ng_ul", "abs_260_280", "abs_260_230"]:
                            valor_convertido = float(v)
                        elif k in ["id_libreria", "id_corrida"]:
                            valor_convertido = int(v)
                        elif k.startswith("fecha_"):
                            valor_convertido = date.fromisoformat(str(v).strip())
                        else:
                            valor_convertido = str(v).strip()
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=422,
                            detail=f"Valor inválido para el campo {k} en la determinación {id_det}."
                        )
                    setattr(db_sub, k, valor_convertido)

            if tipo_interfaz == "libreria":
                db_sub.id_libreria = id_det

            for column in TablaMapeada.__table__.columns:
                if not column.nullable and getattr(db_sub, column.name, None) is None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"El campo {column.name} es obligatorio para {tipo_interfaz}."
                    )

            if tipo_interfaz == "libreria":
                sec_asociada = db.query(db_sql.SecuenciacionTable).join(
                    db_sql.DeterminacionTable,
                    db_sql.SecuenciacionTable.id_determinacion == db_sql.DeterminacionTable.id_determinacion
                ).filter(
                    db_sql.DeterminacionTable.id_muestra == db_det.id_muestra
                ).first()
                if sec_asociada:
                    sec_asociada.id_libreria = id_det

            if tipo_interfaz == "secuenciacion":
                librerias_muestra = db.query(db_sql.LibreriaTable).join(
                    db_sql.DeterminacionTable,
                    db_sql.LibreriaTable.id_determinacion == db_sql.DeterminacionTable.id_determinacion
                ).filter(
                    db_sql.DeterminacionTable.id_muestra == db_det.id_muestra,
                    db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
                ).order_by(db_sql.DeterminacionTable.id_determinacion.asc()).all()

                if librerias_muestra:
                    secs_muestra = db.query(db_sql.DeterminacionTable).filter(
                        db_sql.DeterminacionTable.id_muestra == db_det.id_muestra,
                        db_sql.DeterminacionTable.nombre_determinacion == "secuenciacion",
                        db_sql.DeterminacionTable.estado_determinacion != EstadoDeterminacionEnum.ELIMINADA.value
                    ).order_by(db_sql.DeterminacionTable.id_determinacion.asc()).all()
                    idx = next(
                        (index for index, sec in enumerate(secs_muestra) if sec.id_determinacion == id_det),
                        0
                    )
                    idx_libreria = min(idx, len(librerias_muestra) - 1)
                    db_sub.id_libreria = librerias_muestra[idx_libreria].id_libreria

            db.flush()
            db.expire(db_det)
            evaluar_y_actualizar_estado_determinacion(db_det.id_determinacion, db)

        db.commit()
        return {"status": "success", "message": f"Se actualizaron {len(filas)} determinaciones analizadas."}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

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
    if not isinstance(nombre_corrida, str) or not nombre_corrida.strip():
        raise HTTPException(status_code=422, detail="El nombre de la corrida no puede estar vacío.")
    if not isinstance(id_tecnologia_raw, str):
        raise HTTPException(status_code=422, detail="La plataforma de la corrida debe ser una cadena.")

    id_tecnologia = id_tecnologia_raw.lower().strip()
    fecha_str = payload.get("fecha_corrida")
    try:
        fecha_obj = date.fromisoformat(fecha_str) if fecha_str else date.today()
        cantidad_inicial_poros = (
            int(payload.get("cantidad_inicial_poros"))
            if payload.get("cantidad_inicial_poros") else 0
        )
        passing_filter = float(payload["passing_filter"]) if payload.get("passing_filter") else None
        clustering = float(payload["clustering"]) if payload.get("clustering") else None
        q30 = float(payload["q30"]) if payload.get("q30") else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="La fecha o los valores numéricos de la corrida tienen un formato inválido.")
    estado = payload.get("estado_corrida") or EstadoCorridaEnum.PLANIFICADA.value
    
    db_corrida = db_sql.CorridaTable(
        nombre_corrida=nombre_corrida.strip(),
        fecha_corrida=fecha_obj,
        id_tecnologia_plataforma=id_tecnologia,
        equipo_corrida=payload.get("equipo_corrida"),
        tipo_cartucho=payload.get("tipo_cartucho"),
        estado_corrida=estado,
        yield_data=payload.get("yield_data") if payload.get("yield_data") != "" else None,
        comentario_corrida=payload.get("comentario_corrida") if payload.get("comentario_corrida") != "" else None
    )
    
    db.add(db_corrida)
    db.flush()

    if id_tecnologia == "nanopore":
        db_nano = db_sql.NanoporeTable(
            id_corrida=db_corrida.id_corrida,
            modo_basecalling=payload.get("modo_basecalling", "FAS"),
            cantidad_inicial_poros=cantidad_inicial_poros,
            tiempo_final_corrida=payload.get("tiempo_final_corrida") if payload.get("tiempo_final_corrida") != "" else None
        )
        db.add(db_nano)
        
    elif id_tecnologia == "illumina":
        db_illu = db_sql.IlluminaTable(
            id_corrida=db_corrida.id_corrida,
            mail_basespace=payload.get("mail_basespace", ""),
            passing_filter=passing_filter,
            clustering=clustering,
            q30=q30
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

    if "nombre_corrida" in payload and (
        not isinstance(payload["nombre_corrida"], str) or not payload["nombre_corrida"].strip()
    ):
        raise HTTPException(status_code=422, detail="El nombre de la corrida no puede estar vacío.")

    tecnologia_actual = db_corrida.id_tecnologia_plataforma.lower().strip()
    id_tecnologia = payload.get("id_tecnologia_plataforma", tecnologia_actual)
    if not isinstance(id_tecnologia, str) or id_tecnologia.lower().strip() not in {"illumina", "nanopore"}:
        raise HTTPException(status_code=422, detail="La plataforma debe ser illumina o nanopore.")
    id_tecnologia = id_tecnologia.lower().strip()

    fecha_corrida = None
    if "fecha_corrida" in payload and payload["fecha_corrida"] not in (None, ""):
        try:
            fecha_corrida = date.fromisoformat(payload["fecha_corrida"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="La fecha de la corrida tiene un formato inválido.")

    # 1. Actualizar Datos de la Tabla Base Corrida
    if "nombre_corrida" in payload: db_corrida.nombre_corrida = payload["nombre_corrida"].strip()
    if "fecha_corrida" in payload:
        db_corrida.fecha_corrida = fecha_corrida
    if "equipo_corrida" in payload: db_corrida.equipo_corrida = payload["equipo_corrida"]
    if "tipo_cartucho" in payload:
        if not isinstance(payload["tipo_cartucho"], str) or not payload["tipo_cartucho"].strip():
            raise HTTPException(status_code=422, detail="El tipo de cartucho es obligatorio.")
        db_corrida.tipo_cartucho = payload["tipo_cartucho"].strip()
    if "estado_corrida" in payload: db_corrida.estado_corrida = payload["estado_corrida"]
    if "yield_data" in payload: db_corrida.yield_data = payload["yield_data"] if payload["yield_data"] != "" else None
    if "comentario_corrida" in payload: db_corrida.comentario_corrida = payload["comentario_corrida"] if payload["comentario_corrida"] != "" else None
    db_corrida.id_tecnologia_plataforma = id_tecnologia

    # 2. Actualizar Sub-Tablas dependientes según Plataforma (CON VALIDADORES BLINDADOS)
    try:
        if id_tecnologia == "illumina":
            if tecnologia_actual == "nanopore":
                db_nano_anterior = db.query(db_sql.NanoporeTable).filter(
                    db_sql.NanoporeTable.id_corrida == id_corrida
                ).first()
                if db_nano_anterior:
                    db.delete(db_nano_anterior)
                    db.flush()
            db_illu = db.query(db_sql.IlluminaTable).filter(db_sql.IlluminaTable.id_corrida == id_corrida).first()

            if db_illu:
                if "mail_basespace" in payload:
                    mail_basespace = payload["mail_basespace"]
                    if not isinstance(mail_basespace, str) or not mail_basespace.strip():
                        raise HTTPException(status_code=422, detail="El mail BaseSpace es obligatorio para Illumina.")
                    db_illu.mail_basespace = mail_basespace.strip()
                
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
            else:
                mail_basespace = payload.get("mail_basespace")
                if not isinstance(mail_basespace, str) or not mail_basespace.strip():
                    raise HTTPException(status_code=422, detail="El mail BaseSpace es obligatorio para Illumina.")
                db.add(db_sql.IlluminaTable(
                    id_corrida=id_corrida,
                    mail_basespace=mail_basespace.strip(),
                    passing_filter=float(payload["passing_filter"]) if payload.get("passing_filter") not in (None, "") else None,
                    clustering=float(payload["clustering"]) if payload.get("clustering") not in (None, "") else None,
                    q30=float(payload["q30"]) if payload.get("q30") not in (None, "") else None,
                ))

        elif id_tecnologia == "nanopore":
            if tecnologia_actual == "illumina":
                db_illu_anterior = db.query(db_sql.IlluminaTable).filter(
                    db_sql.IlluminaTable.id_corrida == id_corrida
                ).first()
                if db_illu_anterior:
                    db.delete(db_illu_anterior)
                    db.flush()
            db_nano = db.query(db_sql.NanoporeTable).filter(db_sql.NanoporeTable.id_corrida == id_corrida).first()

            if db_nano:
                if "modo_basecalling" in payload: 
                    db_nano.modo_basecalling = payload["modo_basecalling"]
                
                # Control estricto de nulos antes de int()
                if "cantidad_inicial_poros" in payload: 
                    val = payload["cantidad_inicial_poros"]
                    if val in (None, "") or int(val) <= 0:
                        raise HTTPException(status_code=422, detail="La cantidad de poros debe ser un entero positivo.")
                    db_nano.cantidad_inicial_poros = int(val)
                
                if "tiempo_final_corrida" in payload: 
                    db_nano.tiempo_final_corrida = payload["tiempo_final_corrida"] if payload["tiempo_final_corrida"] != "" else None
                
                db.add(db_nano)
            else:
                try:
                    cantidad_poros = int(payload.get("cantidad_inicial_poros"))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail="La cantidad de poros debe ser un entero positivo.")
                if cantidad_poros <= 0:
                    raise HTTPException(status_code=422, detail="La cantidad de poros debe ser un entero positivo.")
                modo_basecalling = payload.get("modo_basecalling")
                if not isinstance(modo_basecalling, str) or not modo_basecalling.strip():
                    raise HTTPException(status_code=422, detail="El modo de basecalling es obligatorio.")
                db.add(db_sql.NanoporeTable(
                    id_corrida=id_corrida,
                    modo_basecalling=modo_basecalling.strip(),
                    cantidad_inicial_poros=cantidad_poros,
                    tiempo_final_corrida=payload.get("tiempo_final_corrida") or None,
                ))

        db.add(db_corrida)
        evaluar_y_actualizar_estado_corrida(id_corrida, db)
        db.commit()
        db.refresh(db_corrida)
        
    except HTTPException:
        db.rollback()
        raise
    except (TypeError, ValueError):
        db.rollback()
        raise HTTPException(status_code=422, detail="Uno o más campos de la corrida tienen un formato inválido.")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error procesando actualización polimórfica: {str(e)}")

    return db_corrida

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)