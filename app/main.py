# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
import app.database as db_sql
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

def actualizar_estado_servicio(id_servicio: int, db: Session):
    """
    Automatiza el cambio de estado de un Servicio según el estado de sus muestras asociadas.
    """
    db_servicio = db.query(db_sql.ServicioTable).filter(db_sql.ServicioTable.id_servicio == id_servicio).first()
    if not db_servicio:
        return

    muestras = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_servicio == id_servicio).all()
    if not muestras:
        return

    estados = [m.estado_muestra for m in muestras]

    if all(est == "entregado" for est in estados):
        db_servicio.estado_servicio = "finalizado"
    elif any(est in ["procesando", "entregado"] for est in estados):
        db_servicio.estado_servicio = "en curso"
    else:
        db_servicio.estado_servicio = "abierto"

    db.commit()

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

# =====================================================================
# --- ENDPOINTS: MUESTRAS Y METADATA CLÍNICA ---
# =====================================================================

@app.get("/muestras/", response_model=List[schemas.MuestraResponse])
def listar_muestras(id_servicio: Optional[int] = None, db: Session = Depends(db_sql.get_db)):
    """
    Lista todas las muestras, o las filtra por id_servicio si viene el query parameter.
    """
    query = db.query(db_sql.MuestraTable)
    if id_servicio is not None:
        query = query.filter(db_sql.MuestraTable.id_servicio == id_servicio)
    return query.all()

@app.patch("/muestras/{id_muestra}/estado", response_model=schemas.MuestraResponse)
def actualizar_estado_muestra(id_muestra: int, estado: schemas.EstadoMuestraEnum, db: Session = Depends(db_sql.get_db)):
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if not db_muestra:
        raise HTTPException(status_code=404, detail="Muestra no encontrada")
    
    db_muestra.estado_muestra = estado.value
    db.commit()
    db.refresh(db_muestra)
    
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
    db: Session = Depends(db_sql.get_db)
):
    """
    Recibe el lote estructurado del formulario de planificación masiva
    e inicializa las determinaciones y bloques técnicos correspondientes
    respetando el esquema exacto de SQLAlchemy.
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

        db_muestra.estado_muestra = "procesando"

        det_simples = item.get("determinaciones_simples", [])
        for det_nombre in det_simples:
            # Crear cabecera unificada en DeterminacionTable
            nueva_det = db_sql.DeterminacionTable(
                id_muestra=id_muestra,
                nombre_determinacion=det_nombre
            )
            db.add(nueva_det)
            db.flush()

            if det_nombre == "extraccion_adn":
                db.add(db_sql.ExtraccionADNTable(id_determinacion=nueva_det.id_determinacion))
            elif det_nombre == "analisis_fragmento":
                db.add(db_sql.AnalisisFragmentoTable(id_determinacion=nueva_det.id_determinacion))
            elif det_nombre == "cuantificacion":
                db.add(db_sql.CuantificacionTable(id_determinacion=nueva_det.id_determinacion))

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
                nombre_determinacion=f"libreria_secuenciacion_tanda_{orden_lib}"
            )
            db.add(nueva_det_lib)
            db.flush()

            # --- CASO A: REQUIERE LIBRERÍA + SECUENCIACIÓN ---
            kit_usuario = lib.get("kit_utilizado")
            
            if kit_usuario and kit_usuario.strip() != "":
                db_lib = db_sql.LibreriaTable(
                    id_determinacion=nueva_det_lib.id_determinacion,
                    kit=kit_usuario.strip()
                )
                db.add(db_lib)
            else:
                pass

            # --- REGISTRO DE SECUENCIACIÓN (SIEMPRE SE CREA) ---
            cartucho_usuario = lib.get("tipo_cartucho")
            if not cartucho_usuario or cartucho_usuario.strip() == "":
                cartucho_valor = "Pendiente"
            else:
                cartucho_valor = cartucho_usuario.strip()

            db_sec = db_sql.SecuenciacionTable(
                id_determinacion=nueva_det_lib.id_determinacion,
                tipo_cartucho=cartucho_valor
            )
            db.add(db_sec)
        
        actualizar_estado_servicio(db_muestra.id_servicio, db)

    db.commit()
    return {"message": f"Planificación inicializada con éxito."}

@app.post("/muestras/{id_muestra}/extraccion_adn/", response_model=schemas.DeterminacionResponse)
def agregar_extraccion_adn(id_muestra: int, data: schemas.ExtraccionADNSchema, db: Session = Depends(db_sql.get_db)):
    db_det = db_sql.DeterminacionTable(
        id_muestra=id_muestra,
        nombre_determinacion="extraccion_adn"
    )
    db.add(db_det)
    db.flush()

    db_frag = db_sql.ExtraccionADNTable(**data.model_dump(), id_determinacion=db_det.id_determinacion)
    db.add(db_frag)
    
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if db_muestra:
        db_muestra.estado_muestra = "procesando"

    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(db_det)
    return db_det

@app.post("/muestras/{id_muestra}/analisis_fragmento/", response_model=schemas.DeterminacionResponse)
def agregar_analisis_fragmento(id_muestra: int, data: schemas.AnalisisFragmentoSchema, db: Session = Depends(db_sql.get_db)):
    db_det = db_sql.DeterminacionTable(
        id_muestra=id_muestra,
        nombre_determinacion="analisis_fragmento"
    )
    db.add(db_det)
    db.flush()

    db_frag = db_sql.AnalisisFragmentoTable(**data.model_dump(), id_determinacion=db_det.id_determinacion)
    db.add(db_frag)
    
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if db_muestra:
        db_muestra.estado_muestra = "procesando"

    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(db_det)
    return db_det

@app.post("/muestras/{id_muestra}/cuantificacion/", response_model=schemas.DeterminacionResponse)
def agregar_cuantificacion(id_muestra: int, data: schemas.CuantificacionSchema, db: Session = Depends(db_sql.get_db)):
    db_det = db_sql.DeterminacionTable(
        id_muestra=id_muestra,
        nombre_determinacion="cuantificacion"
    )
    db.add(db_det)
    db.flush()

    db_cuanti = db_sql.CuantificacionTable(**data.model_dump(), id_determinacion=db_det.id_determinacion)
    db.add(db_cuanti)
    
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if db_muestra:
        db_muestra.estado_muestra = "procesando"

    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(db_det)
    return db_det

@app.post("/muestras/{id_muestra}/libreria/", response_model=schemas.DeterminacionResponse)
def agregar_libreria(id_muestra: int, data: schemas.LibreriaSchema, db: Session = Depends(db_sql.get_db)):
    db_det = db_sql.DeterminacionTable(
        id_muestra=id_muestra,
        nombre_determinacion="libreria"
    )
    db.add(db_det)
    db.flush()

    db_lib = db_sql.LibreriaTable(**data.model_dump(), id_determinacion=db_det.id_determinacion)
    db.add(db_lib)
    
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if db_muestra:
        db_muestra.estado_muestra = "procesando"
    
    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(db_det)
    return db_det

@app.post("/muestras/{id_muestra}/secuenciacion/", response_model=schemas.DeterminacionResponse)
def agregar_secuenciacion(id_muestra: int, data: schemas.SecuenciacionSchema, db: Session = Depends(db_sql.get_db)):
    db_det = db_sql.DeterminacionTable(
        id_muestra=id_muestra,
        nombre_determinacion="secuenciacion"
    )
    db.add(db_det)
    db.flush()

    db_sec = db_sql.SecuenciacionTable(**data.model_dump(), id_determinacion=db_det.id_determinacion)
    db.add(db_sec)
    
    db_muestra = db.query(db_sql.MuestraTable).filter(db_sql.MuestraTable.id_muestra == id_muestra).first()
    if db_muestra:
        db_muestra.estado_muestra = "procesando"
    
    db.commit()
    actualizar_estado_servicio(db_muestra.id_servicio, db)
    db.refresh(db_det)
    return db_det

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)