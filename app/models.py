# app/models.py
from pydantic import BaseModel, EmailStr, ConfigDict, validator
from typing import Optional, List
from datetime import date
from decimal import Decimal
from enum import Enum

# ==========================================
# 0. DEFINICIÓN DE ENUMS
# ==========================================

class EstadoServicioEnum(str, Enum):
    ABIERTO = "abierto"
    EN_CURSO = "en curso"
    FINALIZADO = "finalizado"
    CANCELADO = "cancelado"

class EstadoMuestraEnum(str, Enum):
    PENDIENTE = "pendiente"
    PROCESANDO = "procesando"
    ENTREGADO = "entregado"
    ELIMINADO = "eliminado"

class EstadoDeterminacionEnum(str, Enum):
    PLANIFICADA = "planificada"
    COMPLETADA = "completada"
    ELIMINADA = "eliminada"

class NombrePlataformaEnum(str, Enum):
    ILLUMINA = "illumina"
    NANOPORE = "nanopore"
    AMBAS = "ambas"
    NO_APLICA = "no aplica"

class NombreDeterminacionEnum(str, Enum):
    EXTRACCION_ADN = "extraccion_adn"
    ANALISIS_FRAGMENTO = "analisis_fragmento"
    CUANTIFICACION = "cuantificacion"
    LIBRERIA = "libreria"
    SECUENCIACION = "secuenciacion"

# ==========================================
# 1. SECCIÓN DE USUARIOS
# ==========================================

class UsuarioBase(BaseModel):
    instituto: str
    abreviacion: str
    nombre: str
    apellido: str
    mail: EmailStr
    origen_geografico: Optional[str] = None

class UsuarioCreate(UsuarioBase):
    pass

class UsuarioResponse(UsuarioBase):
    id_usuario: int
    model_config = ConfigDict(from_attributes=True)

class UsuarioUpdate(BaseModel):
    instituto: Optional[str] = None
    abreviacion: Optional[str] = None
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    mail: Optional[EmailStr] = None
    origen_geografico: Optional[str] = None

# ==========================================
# 2. SECCIÓN DE CONVENIOS
# ==========================================

class ConvenioBase(BaseModel):
    fecha_convenio: date
    nombre_convenio: str
    detalle_convenio: Optional[str] = None
    cantidad_muestras_total: int
    objetivo_convenio: Optional[str] = None
    comentario_convenio: Optional[str] = None

class ConvenioCreate(ConvenioBase):
    pass

class ConvenioResponse(ConvenioBase):
    id_convenio: int
    model_config = ConfigDict(from_attributes=True)

class ConvenioUpdate(BaseModel):
    fecha_convenio: Optional[date] = None
    nombre_convenio: Optional[str] = None
    detalle_convenio: Optional[str] = None
    cantidad_muestras_total: Optional[int] = None
    objetivo_convenio: Optional[str] = None
    comentario_convenio: Optional[str] = None

# ==========================================
# 3. SECCIÓN DE SERVICIOS
# ==========================================

class ServicioBase(BaseModel):
    id_usuario: int
    id_convenio: Optional[int] = None
    fecha_servicio: date
    cantidad_muestras: int
    estado_servicio: EstadoServicioEnum = EstadoServicioEnum.ABIERTO
    detalle_servicio: Optional[str] = None
    objetivo_servicio: Optional[str] = None
    comentario_servicio: Optional[str] = None

class ServicioUpdate(BaseModel):
    id_usuario: Optional[int] = None
    id_convenio: Optional[int] = None
    fecha_servicio: Optional[date] = None
    cantidad_muestras: Optional[int] = None
    detalle_servicio: Optional[str] = None
    objetivo_servicio: Optional[str] = None
    estado_servicio: Optional[EstadoServicioEnum] = None
    comentario_servicio: Optional[str] = None

# ==========================================
# 4. SECCIÓN DE COBROS
# ==========================================

class CobroBase(BaseModel):
    id_presupuesto: Optional[str] = None
    monto: Decimal
    fecha_cobro: Optional[date] = None
    id_factura: Optional[str] = None
    id_comprobante_pago: Optional[str] = None
    comentario_cobro: Optional[str] = None

class CobroCreate(CobroBase):
    pass

class CobroResponse(CobroBase):
    id_servicio: int
    model_config = ConfigDict(from_attributes=True)

class CobroUpdate(BaseModel):
    id_servicio: Optional[int] = None
    id_presupuesto: Optional[str] = None
    monto: Optional[Decimal] = None
    fecha_cobro: Optional[date] = None
    id_factura: Optional[str] = None
    id_comprobante_pago: Optional[str] = None
    comentario_cobro: Optional[str] = None

# ==========================================
# 5. SECCIÓN DE MUESTRAS
# ==========================================

class MuestraBase(BaseModel):
    id_servicio: int
    nombre_muestra: str = None
    nro_ANLIS: Optional[str] = None
    fecha_recepcion: date
    tecnologia_requerida: str
    servicio_requerido: str
    tipo_de_muestra: str
    organismo_esperado: str
    tamano_genoma_amplicon: int
    reads_profundidad_requerida: str
    analisis_requerido: str
    comentario_muestra: Optional[str] = None
    fecha_entrega: Optional[date] = None
    estado_muestra: EstadoMuestraEnum = EstadoMuestraEnum.PENDIENTE

    @validator('tamano_genoma_amplicon')
    def check_positive_int(cls, v):
        if v is not None and v <= 0:
            raise ValueError("El valor debe ser un número positivo")
        return v

class MuestraCreate(MuestraBase):
    pass

class ServicioCreate(ServicioBase):
    muestras: List[MuestraCreate] = []

class MetadataClinicaBase(BaseModel):
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    fecha_nacimiento: Optional[date] = None
    edad: Optional[int] = None
    fecha_toma: Optional[date] = None
    fecha_extraccion: Optional[date] = None
    origen_muestra: Optional[str] = None
    indice_padre_madre: str
    fecha_informe: Optional[date] = None
    variante1: Optional[str] = None
    cigosidad1: Optional[str] = None
    clasificacion1: Optional[str] = None
    variante2: Optional[str] = None
    cigosidad2: Optional[str] = None
    clasificacion2: Optional[str] = None
    comentario_metadata: Optional[str] = None

class MetadataClinicaResponse(MetadataClinicaBase):
    id_muestra: int
    model_config = ConfigDict(from_attributes=True)

class MuestraResponse(MuestraBase):
    id_muestra: int
    metadata_clinica: Optional[MetadataClinicaResponse] = None
    model_config = ConfigDict(from_attributes=True)

class ServicioResponse(ServicioBase):
    id_servicio: int
    muestras: List[MuestraResponse] = []
    cobro: Optional[CobroResponse] = None
    estado_servicio: EstadoServicioEnum
    model_config = ConfigDict(from_attributes=True)

class MuestraUpdate(BaseModel):
    nombre_muestra: Optional[str] = None
    nro_ANLIS: Optional[str] = None
    tipo_de_muestra: Optional[str] = None
    fecha_recepcion: Optional[date] = None
    servicio_requerido: Optional[str] = None
    tecnologia_requerida: Optional[str] = None
    organismo_esperado: Optional[str] = None
    tamano_genoma_amplicon: Optional[int] = None
    reads_profundidad_requerida: Optional[str] = None
    analisis_requerido: Optional[str] = None
    comentario_muestra: Optional[str] = None    
    estado_muestra: Optional[EstadoMuestraEnum] = None
    fecha_entrega: Optional[date] = None

# ==========================================
# 6. SECCIÓN DE METADATA CLÍNICA
# ==========================================

class MetadataClinicaCreate(MetadataClinicaBase):
    id_muestra: int

class MetadataClinicaUpdate(BaseModel):
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    fecha_nacimiento: Optional[date] = None
    edad: Optional[int] = None
    fecha_toma: Optional[date] = None
    fecha_extraccion: Optional[date] = None
    origen_muestra: Optional[str] = None
    indice_padre_madre: Optional[str] = None
    fecha_informe: Optional[date] = None
    variante1: Optional[str] = None
    cigosidad1: Optional[str] = None
    clasificacion1: Optional[str] = None
    variante2: Optional[str] = None
    cigosidad2: Optional[str] = None
    clasificacion2: Optional[str] = None
    comentario_metadata: Optional[str] = None

# ==========================================
# 7. SECCIÓN DE DETERMINACIONES
# ==========================================

class ExtraccionADNSchema(BaseModel):
    fecha_extraccion_adn: Optional[date] = None
    comentario_extraccion_adn: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class ExtraccionADNUpdate(BaseModel):
    fecha_extraccion_adn: Optional[date] = None
    comentario_extraccion_adn: Optional[str] = None

class AnalisisFragmentoSchema(BaseModel):
    fecha_analisis_fragmento: Optional[date] = None
    equipo_analisis_fragmento: Optional[str] = None
    resultado_analisis: Optional[str] = None
    comentario_analisis_fragmento: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class AnalisisFragmentoUpdate(BaseModel):
    fecha_analisis_fragmento: Optional[date] = None
    equipo_analisis_fragmento: Optional[str] = None
    resultado_analisis: Optional[str] = None
    comentario_analisis_fragmento: Optional[str] = None

class CuantificacionSchema(BaseModel):
    fecha_cuantificacion: Optional[date] = None
    equipo_cuantificacion: Optional[str] = None
    concentracion_ng_ul: Optional[float] = None
    abs_260_280: Optional[float] = None
    abs_260_230: Optional[float] = None
    comentario_cuantificacion: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class CuantificacionUpdate(BaseModel):
    fecha_cuantificacion: Optional[date] = None
    equipo_cuantificacion: Optional[str] = None
    concentracion_ng_ul: Optional[float] = None
    abs_260_280: Optional[float] = None
    abs_260_230: Optional[float] = None
    comentario_cuantificacion: Optional[str] = None

class LibreriaSchema(BaseModel):
    fecha_libreria: Optional[date] = None
    nombre_libreria: Optional[str] = None
    kit: str
    proceso: Optional[str] = None
    index_set: Optional[str] = None
    index_well_barcode: Optional[str] = None
    comentario_libreria: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class LibreriaUpdate(BaseModel):
    fecha_libreria: Optional[date] = None
    nombre_libreria: Optional[str] = None
    kit: Optional[str] = None
    proceso: Optional[str] = None
    index_set: Optional[str] = None
    index_well_barcode: Optional[str] = None
    comentario_libreria: Optional[str] = None

class SecuenciacionSchema(BaseModel):
    id_corrida: Optional[int] = None
    qcheck: Optional[str] = None
    kraken: Optional[str] = None
    profundidad_estimada: Optional[str] = None
    se_repite: Optional[str] = None
    analisis_bioinformatico: Optional[str] = None
    ubicacion_servidor: Optional[str] = None
    comentario_secuenciacion: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class SecuenciacionUpdate(BaseModel):
    id_corrida: Optional[int] = None
    qcheck: Optional[str] = None
    kraken: Optional[str] = None
    profundidad_estimada: Optional[str] = None
    se_repite: Optional[str] = None
    analisis_bioinformatico: Optional[str] = None
    ubicacion_servidor: Optional[str] = None
    comentario_secuenciacion: Optional[str] = None

class DeterminacionBase(BaseModel):
    id_muestra: int
    nombre_determinacion: NombreDeterminacionEnum
    estado_determinacion: EstadoDeterminacionEnum = EstadoDeterminacionEnum.PLANIFICADA

    @validator('nombre_determinacion')
    def check_enum_value(cls, v):
        if v not in [e.value for e in NombreDeterminacionEnum]:
            raise ValueError("El valor debe ser uno de los valores permitidos")
        return v
    
class DeterminacionCreate(DeterminacionBase):
    pass

class DeterminacionResponse(DeterminacionBase):
    id_determinacion: int
    estado_determinacion: EstadoDeterminacionEnum
    extraccion_adn: Optional[ExtraccionADNSchema] = None
    analisis_fragmento: Optional[AnalisisFragmentoSchema] = None
    cuantificacion: Optional[CuantificacionSchema] = None
    libreria: Optional[LibreriaSchema] = None
    secuenciacion: Optional[SecuenciacionSchema] = None
    model_config = ConfigDict(from_attributes=True)

class DeterminacionUpdate(BaseModel):
    id_muestra: Optional[int] = None
    nombre_determinacion: Optional[NombreDeterminacionEnum] = None
    estado_determinacion: Optional[EstadoDeterminacionEnum] = None

# ==========================================
# 8. SECCIÓN DE CORRIDAS
# ==========================================

class NanoporeSchema(BaseModel):
    modo_basecalling: str
    cantidad_inicial_poros: int
    tiempo_final_corrida: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class NanoporeUpdate(BaseModel):
    modo_basecalling: Optional[str] = None
    cantidad_inicial_poros: Optional[int] = None
    tiempo_final_corrida: Optional[str] = None

class IlluminaSchema(BaseModel):
    mail_basespace: EmailStr
    passing_filter: Optional[float] = None
    clustering: Optional[float] = None
    q30: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)

class IlluminaUpdate(BaseModel):
    mail_basespace: Optional[EmailStr] = None
    passing_filter: Optional[float] = None
    clustering: Optional[float] = None
    q30: Optional[float] = None

class CorridaBase(BaseModel):
    nombre_corrida: str
    fecha_corrida: Optional[date] = None
    id_tecnologia_plataforma: NombrePlataformaEnum
    equipo_corrida: str
    tipo_cartucho: str
    yield_data: Optional[str] = None
    comentario_corrida: Optional[str] = None

    @validator('id_tecnologia_plataforma')
    def check_enum_value(cls, v):
        if v not in [e.value for e in NombrePlataformaEnum]:
            raise ValueError("El valor debe ser uno de los valores permitidos")
        return v

class CorridaCreate(CorridaBase):
    pass

class CorridaResponse(CorridaBase):
    id_corrida: int
    nombre_corrida: str
    fecha_corrida: Optional[date] = None
    id_tecnologia_plataforma: NombrePlataformaEnum
    equipo_corrida: str
    tipo_cartucho: str
    yield_data: Optional[str] = None
    comentario_corrida: Optional[str] = None
    nanopore: Optional[NanoporeSchema] = None
    illumina: Optional[IlluminaSchema] = None
    model_config = ConfigDict(from_attributes=True)

class CorridaUpdate(BaseModel):
    nombre_corrida: Optional[str] = None
    fecha_corrida: Optional[date] = None
    id_tecnologia_plataforma: Optional[NombrePlataformaEnum] = None
    equipo_corrida: Optional[str] = None
    tipo_cartucho: Optional[str] = None
    yield_data: Optional[str] = None
    comentario_corrida: Optional[str] = None