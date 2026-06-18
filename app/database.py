# app/database.py
from sqlalchemy import create_engine, Column, Integer, String, Date, Text, Numeric, ForeignKey, Double
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

#SQLALCHEMY_DATABASE_URL = "mysql+pymysql://user:password@localhost/CNGB_DDBB"
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- MODELOS ORM ---

class UsuarioTable(Base):
    __tablename__ = "Usuario"
    id_usuario = Column(Integer, primary_key=True, index=True)
    instituto = Column(String(150), nullable=False)
    abreviacion = Column(String(50), nullable=False)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    mail = Column(String(150), unique=True, nullable=False)
    origen_geografico = Column(String(100))

    servicios = relationship("ServicioTable", back_populates="usuario", cascade="all, delete-orphan")

class ConvenioTable(Base):
    __tablename__ = "Convenio"
    id_convenio = Column(Integer, primary_key=True, index=True)
    fecha_convenio = Column(Date, nullable=False)
    nombre_convenio = Column(Text, nullable=False)
    detalle_convenio = Column(Text)
    cantidad_muestras_total = Column(Integer, nullable=False)
    objetivo_convenio = Column(Text)
    comentario_convenio = Column(Text)

    servicios = relationship("ServicioTable", back_populates="convenio")

class ServicioTable(Base):
    __tablename__ = "Servicio"
    id_servicio = Column(Integer, primary_key=True, index=True)
    id_usuario = Column(Integer, ForeignKey("Usuario.id_usuario", ondelete="CASCADE"), nullable=False)
    id_convenio = Column(Integer, ForeignKey("Convenio.id_convenio", ondelete="SET NULL"), nullable=True)
    fecha_servicio = Column(Date, nullable=False)
    detalle_servicio = Column(Text)
    cantidad_muestras = Column(Integer, nullable=False)
    objetivo_servicio = Column(String(50))
    estado_servicio = Column(String(50))
    comentario_servicio = Column(Text)

    usuario = relationship("UsuarioTable", back_populates="servicios")
    convenio = relationship("ConvenioTable", back_populates="servicios")
    cobro = relationship("CobroTable", back_populates="servicio", uselist=False, cascade="all, delete-orphan")
    muestras = relationship("MuestraTable", back_populates="servicio", cascade="all, delete-orphan")

class CobroTable(Base):
    __tablename__ = "Cobro"
    id_servicio = Column(Integer, ForeignKey("Servicio.id_servicio", ondelete="CASCADE"), primary_key=True)
    id_presupuesto = Column(String(50))
    monto = Column(Numeric(15, 2), nullable=False)
    fecha_cobro = Column(Date)
    id_factura = Column(String(50))
    id_comprobante_pago = Column(String(50))
    comentario_cobro = Column(Text)

    servicio = relationship("ServicioTable", back_populates="cobro")

class MuestraTable(Base):
    __tablename__ = "Muestra"
    id_muestra = Column(Integer, primary_key=True, index=True)
    id_servicio = Column(Integer, ForeignKey("Servicio.id_servicio", ondelete="CASCADE"), nullable=False)
    nombre_muestra = Column(String(100), nullable=False)
    nro_ANLIS = Column(String(50))
    fecha_recepcion = Column(Date, nullable=False)
    tecnologia_requerida = Column(String(100), nullable=False)
    servicio_requerido = Column(String(150), nullable=False)
    tipo_de_muestra = Column(String(100), nullable=False)
    organismo_esperado = Column(String(150), nullable=False)
    tamano_genoma_amplicon = Column(Integer, nullable=False)
    reads_profundidad_requerida = Column(String(50), nullable=False)
    analisis_requerido = Column(String(200), nullable=False)
    comentario_muestra = Column(Text)
    estado_muestra = Column(String(50))

    servicio = relationship("ServicioTable", back_populates="muestras")
    metadata_clinica = relationship("MetadataClinicaTable", back_populates="muestra", uselist=False, cascade="all, delete-orphan")
    determinaciones = relationship("DeterminacionTable", back_populates="muestra", cascade="all, delete-orphan")

class MetadataClinicaTable(Base):
    __tablename__ = "Metadata_Clinica"
    id_muestra = Column(Integer, ForeignKey("Muestra.id_muestra", ondelete="CASCADE"), primary_key=True)
    nombre = Column(String(100))
    apellido = Column(String(100))
    fecha_nacimiento = Column(Date)
    edad = Column(Integer)
    fecha_toma = Column(Date)
    fecha_extraccion = Column(Date)
    origen_muestra = Column(String(100))
    indice_padre_madre = Column(String(50), nullable=False)
    fecha_informe =Column(Date)
    variante1 = Column(Text)
    cigosidad1 =Column(String(100))
    clasificacion1 = Column(String(100))
    variante2 = Column(Text)
    cigosidad2 =Column(String(100))
    clasificacion2 = Column(String(100))
    comentario_metadata = Column(Text)

    muestra = relationship("MuestraTable", back_populates="metadata_clinica")

class DeterminacionTable(Base):
    __tablename__ = "Determinacion"
    id_determinacion = Column(Integer, primary_key=True, index=True)
    id_muestra = Column(Integer, ForeignKey("Muestra.id_muestra", ondelete="CASCADE"), nullable=False)
    nombre_determinacion = Column(String(200), nullable=False)
    
    muestra = relationship("MuestraTable", back_populates="determinaciones")
    extraccion_adn = relationship("ExtraccionADNTable", back_populates="determinacion", uselist=False, cascade="all, delete-orphan")
    analisis_fragmento = relationship("AnalisisFragmentoTable", back_populates="determinacion", uselist=False, cascade="all, delete-orphan")
    cuantificacion = relationship("CuantificacionTable", back_populates="determinacion", uselist=False, cascade="all, delete-orphan")
    libreria = relationship("LibreriaTable", back_populates="determinacion", uselist=False, cascade="all, delete-orphan")
    secuenciacion = relationship("SecuenciacionTable", back_populates="determinacion", uselist=False, cascade="all, delete-orphan")

class ExtraccionADNTable(Base):
    __tablename__ = "Extraccion_ADN"
    id_determinacion = Column(Integer, ForeignKey("Determinacion.id_determinacion", ondelete="CASCADE"), primary_key=True)
    fecha_extraccion_adn = Column(Date)
    comentario_extraccion_adn = Column(Text)

    determinacion = relationship("DeterminacionTable", back_populates="extraccion_adn")

class AnalisisFragmentoTable(Base):
    __tablename__ = "Analisis_Fragmento"
    id_determinacion = Column(Integer, ForeignKey("Determinacion.id_determinacion", ondelete="CASCADE"), primary_key=True)
    fecha_analisis_fragmento = Column(Date)
    equipo_analisis_fragmento = Column(String(50))
    resultado_analisis = Column(String(10))
    comentario_analisis_fragmento = Column(Text)
    
    determinacion = relationship("DeterminacionTable", back_populates="analisis_fragmento")

class CuantificacionTable(Base):
    __tablename__ = "Cuantificacion"
    id_determinacion = Column(Integer, ForeignKey("Determinacion.id_determinacion", ondelete="CASCADE"), primary_key=True)
    fecha_cuantificacion = Column(Date)
    equipo_cuantificacion = Column(String(50))
    concentracion_ng_ul = Column(Double)
    abs_260_280 = Column(Double)
    abs_260_230 = Column(Double)
    comentario_cuantificacion = Column(Text)
    
    determinacion = relationship("DeterminacionTable", back_populates="cuantificacion")

class LibreriaTable(Base):
    __tablename__ = "Libreria"
    id_determinacion = Column(Integer, ForeignKey("Determinacion.id_determinacion", ondelete="CASCADE"), primary_key=True)
    fecha_libreria = Column(Date)
    nombre_pool = Column(String(50))
    kit = Column(String(150), nullable=False)
    proceso = Column(String(50))
    index_set = Column(String(50))
    index_well_barcode = Column(String(50))
    comentario_libreria = Column(Text)

    determinacion = relationship("DeterminacionTable", back_populates="libreria")

class SecuenciacionTable(Base):
    __tablename__ = "Secuenciacion"
    id_determinacion = Column(Integer, ForeignKey("Determinacion.id_determinacion", ondelete="CASCADE"), primary_key=True)
    id_corrida = Column(Integer, ForeignKey("Corrida.id_corrida", ondelete="RESTRICT"))
    tipo_cartucho = Column(String(150), nullable=False)
    qcheck = Column(String(10))
    kraken = Column(String(10))
    profundidad_estimada = Column(String(50))
    se_repite = Column(String(10))
    analisis_bioinformatico = Column(Text)
    fecha_entrega = Column(Date)
    ubicacion_servidor = Column(String(255))
    comentario_secuenciacion = Column(Text)
    
    corrida = relationship("CorridaTable", back_populates="secuenciaciones")
    determinacion = relationship("DeterminacionTable", back_populates="secuenciacion")

class CorridaTable(Base):
    __tablename__ = "Corrida"
    id_corrida = Column(Integer, primary_key=True, index=True)
    nombre_corrida = Column(String(200), nullable=False)
    fecha_corrida = Column(Date, nullable=False)
    id_tecnologia_plataforma = Column(String(50), nullable=False)
    equipo_corrida = Column(String(100), nullable=False)
    yield_data = Column(String(50))
    comentario_corrida = Column(Text)

    nanopore = relationship("NanoporeTable", back_populates="corrida", uselist=False, cascade="all, delete-orphan")
    illumina = relationship("IlluminaTable", back_populates="corrida", uselist=False, cascade="all, delete-orphan")
    secuenciaciones = relationship("SecuenciacionTable", back_populates="corrida")

class NanoporeTable(Base):
    __tablename__ = "Nanopore"
    id_corrida = Column(Integer, ForeignKey("Corrida.id_corrida", ondelete="CASCADE"), primary_key=True)
    modo_basecalling = Column(String(10), nullable=False)
    cantidad_inicial_poros = Column(Integer, nullable=False)
    tiempo_final_corrida = Column(String(50))
    lote_flowcell = Column(String(100))

    corrida = relationship("CorridaTable", back_populates="nanopore")

class IlluminaTable(Base):
    __tablename__ = "Illumina"
    id_corrida = Column(Integer, ForeignKey("Corrida.id_corrida", ondelete="CASCADE"), primary_key=True)
    cantidad_ciclos = Column(Integer, nullable=False)
    mail_basespace = Column(String(50), nullable=False)
    passing_filter = Column(Double)
    clustering = Column(Double)
    q30 = Column(Double)
    lote_cartucho = Column(String(100))
    vto_cartucho = Column(Date)

    corrida = relationship("CorridaTable", back_populates="illumina")