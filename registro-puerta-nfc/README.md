# 🔒 Registro de puerta con NFC

Proyecto minimalista para registrar cada vez que se cierra la puerta de casa con llave, usando un sticker NFC pegado junto a la puerta.

## Cómo funciona

1. Al cerrar la puerta con llave, pasas el móvil por el sticker NFC.
2. El sticker abre la URL de registro, que guarda la fecha y hora en la base de datos y muestra una confirmación ("🔒 Puerta cerrada").
3. El portal muestra todos los registros agrupados por día.

No hay app que instalar: el NFC del móvil abre el navegador directamente.

## URLs

| Qué | URL |
|---|---|
| **Portal** (ver registros) | https://registro-puerta-nfc.vercel.app |
| **Registro** (grabar en el sticker) | https://registro-puerta-nfc.vercel.app/registro |

## Configurar el sticker NFC

1. Instala una app para escribir NFC, por ejemplo **NFC Tools** (iOS/Android).
2. Añade un registro de tipo **URL** con la dirección de registro de arriba.
3. Escribe el tag acercando el móvil al sticker. Listo.

Al pasar cualquier móvil con NFC por el sticker se abrirá la página y quedará registrado el cierre. La página evita duplicados si se recarga en el mismo minuto.

## Stack

- **Frontend:** un solo `index.html` estático, sin dependencias ni build. Desplegado en Vercel.
- **Base de datos:** Supabase (proyecto `alerta-boe`, tabla independiente `registros_puerta` con RLS: la clave pública solo puede insertar filas vacías y leerlas).

```sql
create table public.registros_puerta (
  id bigint generated always as identity primary key,
  creado_en timestamptz not null default now()
);
```

## Desarrollo

No hay build: edita `index.html` y despliega. El archivo `vercel.json` añade la ruta limpia `/registro` (también funciona `?r` como parámetro, útil si se sirve como archivo estático sin rewrites).
