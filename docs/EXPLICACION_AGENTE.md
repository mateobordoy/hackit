# Hack It · Nuestro agente comprador, explicado de principio a fin

> Documento para todo el equipo (Burak, Andrey, Jose, Mateo). Explica **qué hace el código, por qué
> está hecho así y cómo contarlo en el pitch**. No hace falta saber programar para entenderlo.
>
> Código: `hackit/` (en la raíz de este repositorio) · Estado: 24 oct 2026, primera versión funcional.

---

## 1. La idea en 30 segundos (para abrir el pitch)

> *"El proveedor no es una persona: es un equipo de departamentos (ventas, finanzas, logística,
> calidad, dirección). Así que nosotros tampoco mandamos a una persona: mandamos un **comité de
> compras** que es su espejo. Nuestro departamento de Finanzas habla el idioma de su Finanzas,
> nuestra Logística el de su Logística. Cada departamento lee al proveedor desde su ángulo y defiende
> su palanca; un Director decide. Y para que ninguna IA se invente un precio, **el código pone todos
> los números**: los agentes solo eligen de un menú de paquetes ya calculados."*

Tres ideas propias que nos diferencian:

1. **Comité espejo**: 4 departamentos + 1 director, cada uno con su lente.
2. **Menú "a coste igual"**: paquetes que **a nosotros nos cuestan lo mismo** pero **al proveedor le
   valen distinto**. Así encontramos intercambios gratis (por ejemplo: pagar antes a quien necesita liquidez).
3. **Brújula + sensor de respuesta**: el agente mide cuánto cede el proveedor por cada euro que
   cedemos nosotros. Cuando deja de responder, ha llegado a su suelo: cerramos.

---

## 2. Repaso rápido del reto (lo que el agente tiene que hacer)

- Somos el comprador de **Hollerau Pumpenwerke**. Cada **caso** = una negociación por e-mail con 1 a 3
  proveedores (bots de Tacto).
- Cada mensaje nuestro lleva un texto y hasta **3 propuestas** ("term sheets": precio, días de pago,
  duración, etc.). **Solo las propuestas son vinculantes**, el texto no.
- Podemos **citar datos** por su código (ej. `idx:PPI-D351@2026-08`); Tacto comprueba si la cifra es
  cierta (`verified`) o falsa (`contradicted`, nos quita confianza).
- **60 segundos** para responder a cada mensaje del proveedor. Máximo ~10 mensajes por proveedor.
- Nota de cada caso = **qué parte del ahorro máximo posible conseguimos** (0 = quedarse con la
  primera oferta; 1 = el mejor trato que ese proveedor aceptaría jamás). No es "% de acierto".
- Final del domingo: 5 rondas de 8 minutos con **proveedores nuevos**, sin intervención humana.

---

## 3. Cómo fluye un caso (el dibujo para la diapositiva)

```
 FASE 0 · ANTES DE LA FINAL (sábado/domingo mañana, sin reloj)
   Expediente de cada proveedor de los datos  →  hackit-dossiers.json
            │
 FASE A · AL EMPEZAR LA RONDA, ANTES DE ABRIR EL CASO (sin reloj de 60 s, ~10 s)
   Ficha del caso → expediente + almacén de argumentos de cada proveedor
   Los 4 departamentos ESTUDIAN al proveedor en paralelo
            │
   ── abrimos el caso: llegan las ofertas iniciales con su PDF; empieza el reloj de 60 s ──
            │
 FASE B · CADA RONDA (~12-16 s de nuestros 60)
   1. Escuchar: ¿qué pide el proveedor en su e-mail? (necesidades en vivo)
   2. Brújula: ¿dónde creemos que acabará? ¿sigue cediendo o ya está firme?
   3. Menú: el código calcula 3-6 paquetes válidos "a coste igual"
   4. Comité: Finanzas · Logística · Calidad · Mercado opinan en paralelo
   5. Director: elige paquete(s) del menú, argumentos, ritmo de cesión y tono
   6. Código: pone el precio exacto, comprueba todo y escribe el e-mail
            │
 CIERRE
   Aceptar la mejor oferta válida (o retirarse si nada mejora nuestra alternativa)
   ACTAS: todo queda escrito en logs/hackit/ para explicar cada decisión
```

---

## 4. Las piezas del código, una a una

Todo está en `starter-kit/hackit/`. Cada archivo es una "pieza" con una sola responsabilidad.

### 4.1 `dossier.py` · El expediente del proveedor (Fase 0, solo código)
**Qué hace:** junta todo lo que sabemos de un proveedor:
- perfil (tipo de dueño: familiar, grupo, fundador; país; tamaño; solvencia);
- cómo le tratamos (¿le pagamos tarde? ¿cuánto le compramos?);
- cómo trabaja (puntualidad, defectos);
- cómo negoció antes (cuánto pidió y en cuánto cerró);
- su contrato;
- y, sobre todo, **sus necesidades**.

**Las pistas escondidas.** Las notas, cartas, noticias y comentarios de presupuestos usan unas
pocas frases tipo. Las hemos catalogado en **13 necesidades**:

| Necesidad | Frases que la delatan (ejemplos) | Qué significa para nosotros |
|---|---|---|
| `cash` (liquidez) | "the bank is watching", "pay our invoices within 10 days", noticia *credit_watch* | Pagarle antes le vale más de lo que nos cuesta |
| `idle_capacity` | "second shift has been on hold", noticia *free_capacity* | Volumen y contrato largo le valen mucho |
| `full_capacity` | "order intake is very strong", noticia *full_order_book* | Cobra la prisa; dale plazo y compromiso |
| `price_frozen` | "discounts not possible (head-office directive)" | El precio no se mueve: negocia pago/condiciones |
| `approval_ladder` | "changes above X % need the approval of their pricing committee" | Pasos pequeños y constantes |
| `stale_costing` | "works from his own spreadsheet, last updated two years ago" | Los índices actuales le desarman |
| `open_book` | "offered to walk us through their calculation" | El argumento del coste estimado funciona |
| `hedged` | "material covered at fixed prices until March" | Una cláusula de índice le vale poco |
| `partnership` | "kept supplying at the old price… expects partnership" | Reconocer la relación, no apretar de más |
| `values_us` | "Hollerau is an important reference customer" | Quiere mantenernos |
| `deprioritising_us` | "reviewing small accounts", "not strategic" | Solo le mueve volumen o varios años |
| `late_payment_grievance` | "invoices are overdue" | Pagar a tiempo ya es una concesión |
| `supply_risk` | noticia *chip_shortage*, *succession* | Asegurar suministro, no forzar |

Además, las **noticias traen una etiqueta de tema** (`credit_watch`, `free_capacity`…), que usamos
directamente. El ruido ("vacaciones", "certificado ISO", "feria") se descarta.

**Para proveedores nuevos** (la final), las mismas frases se buscan **en sus propios e-mails** en
cada ronda (`needs_in`). Si un proveedor desconocido escribe "faster payment helps our cash flow", el
agente apunta `cash` en directo.

### 4.2 `evidence.py` · El almacén de argumentos (solo código)
**Qué hace:** prepara frases con datos **que Tacto puede verificar**, con la cifra exacta sacada de
los mismos datos con los que Tacto comprueba. Así un argumento nunca sale `contradicted`.

Tipos de argumento:
1. **Índice de coste que ha bajado**: busca, para cada componente del coste de la pieza, la ventana
   de hasta 12 meses con la mayor caída (regla de Tacto).
2. **Preaviso insuficiente**: "su carta da 51 días de preaviso, el contrato exige 90".
3. **Coste estimado** (should-cost) por debajo de su precio.
4. **Puntualidad o defectos** malos.
5. **Previsión de demanda al alza** (más volumen para ellos).
6. **Precios de nuestros pedidos recientes.**
7. **Nuestra alternativa**, si es un presupuesto más barato.
8. **En concursos: la oferta del rival más barato**, que se puede citar a cada uno de los demás.

**Filtro de seguridad:** si una cifra **favorece al proveedor** (por ejemplo, una alternativa más
cara que su oferta), no se usa. Reglas de Tacto respetadas: como mucho 3 comprobaciones por mensaje,
cada dato una sola vez por caso.

### 4.3 `menu.py` · El menú "a coste igual" (solo código) ⭐ idea propia
**El problema:** una IA no debe inventarse propuestas, porque puede proponer algo ilegal o caro.
**La solución:**
1. El código prueba **todas las combinaciones permitidas** (días de pago × duración × fecha × …).
2. Elige como **paquete principal (M0)** la combinación que **menos nos cuesta**, cumpliendo los
   requisitos obligatorios.
3. Para cada combinación alternativa, calcula **el precio al que nos costaría exactamente lo mismo**
   que M0. Es decir, todas las opciones del menú nos cuestan igual.
4. Calcula **cuánto vale cada opción para el proveedor**, con la fórmula oficial de Tacto
   (`supplier_value`) y nuestra estimación del proveedor:
   - **su coste del dinero**: más alto si es un taller de fundador o si necesita liquidez;
   - **su coste real**: el should-cost, corregido por país y por su error conocido.
5. Las opciones que **le dan más al proveedor sin costarnos más** son **intercambios gratis**: en
   **efectivo** (pagar antes, fecha de subida más tarde) o en **seguridad** (contrato más largo, más
   volumen, cláusula de índice, más plazo de entrega).

**Ejemplo real (P01, fundición con problemas de caja):**
- M0: 124,70 € pagando a 90 días.
- M1: 122,62 € pagando a 14 días. **Nos cuesta lo mismo**, pero a ellos les vale ~1,7 € más por pieza.
- Por eso el proveedor acepta precios más bajos si le pagamos antes.

### 4.4 `committee.py` + `llm.py` · El comité de compras (la parte con IA) ⭐ idea propia
**Los 5 agentes** (todos son Claude, cada uno con su papel):

| Agente | Su lente | Qué propone |
|---|---|---|
| **Finanzas** | pagos, liquidez, si le pagamos tarde, solvencia | días de pago como moneda de cambio |
| **Logística** | capacidad, plazos, volumen, duración | compromiso, volumen, plazo |
| **Calidad** | puntualidad, defectos, reclamaciones | compromisos de calidad, penalizaciones |
| **Mercado** | índices, coste estimado, rivales, alternativa | el argumento de precio |
| **Director** | escucha a los cuatro | **decide** |

**Dos momentos:**
- **Estudio (Fase A):** los 4 departamentos leen el expediente y apuntan qué necesita el proveedor,
  qué argumentará y qué le podemos ofrecer barato.
- **Reunión (cada ronda):** los 4 opinan **a la vez** (~10 s). Cada uno propone un paquete del menú,
  argumentos y ritmo de cesión. Después el Director (~6 s) decide:
  - **paquete principal** y hasta **2 alternativas** del menú; la elección del proveedor nos revela
    qué valora;
  - **hasta 3 argumentos** del almacén;
  - **ritmo de cesión**: *mantener / poco / normal / mucho*;
  - **tono** y **un párrafo** del e-mail, **sin cifras**;
  - **una pregunta** para aprender qué valora el proveedor;
  - **una frase para las actas** explicando por qué.

**Lo que la IA NO puede hacer:** poner precios, inventar paquetes, inventar datos, aceptar o
retirarse. Solo **elige por código de identificación** (M0, M1…, index:1…). El código valida cada
elección.

**Seguridad técnica (`llm.py`):**
- respuestas en **JSON estructurado** (formato obligatorio);
- **límite de tiempo duro** en cada llamada;
- si la IA tarda o falla, **el código decide solo** y nunca nos pasamos de los 60 s.

**Gestión del tiempo:**
- Con 1 proveedor: comité completo (22 s de presupuesto).
- Con 2 proveedores: solo el director (12 s).
- Con 3 proveedores: solo código, porque los demás proveedores esperan mientras pensamos.

**Conocimiento compartido:** todos los agentes reciben unas "lecciones de la casa" sobre cómo se
comportan estos proveedores:
- ceden solo si nosotros nos movemos;
- los datos verificados en el **primer** e-mail son los que más bajan su primera contraoferta;
- cuando dicen "agreed", el precio se congela;
- amenazas y faroles no funcionan, y las preguntas educadas y los intercambios reales sí.

### 4.5 `buyer.py` · El comprador que lo une todo
Es el "cerebro" que el kit de Tacto ejecuta en cada movimiento. Partes clave:

**a) La brújula ("compass").** Responde a la pregunta *"¿hasta dónde estamos dispuestos a subir ahora?"*.
- **Antes de que el proveedor hable:** una estimación sacada de los datos ("prior"). Depende de:
  - el objetivo de la categoría (en *leverage* hay más margen que en *stabilise*);
  - sus necesidades (con *price_frozen* casi no hay margen; con *idle_capacity*, más);
  - en subidas de precio, cuánto justifican **de verdad** sus índices de coste. Ejemplo: P01 pide
    +9,5 % y los índices justifican +2,3 %.
- **Nunca por debajo de su coste estimado** (no estrangulamos a un proveedor estratégico; en esos
  casos Tacto penaliza dejarle sin margen).
- **Nunca por encima** de su propia contraoferta ni de lo que cuesta nuestra alternativa.

**b) El sensor de respuesta** ⭐ idea propia:
- Mide **cuántos euros cede el proveedor por cada euro que cedemos nosotros**, ronda a ronda.
- Mientras responde (por ejemplo, 0,5–1,3 € por cada euro nuestro), seguimos negociando.
- Si **dos veces seguidas** cede menos de un 25 %, está en su suelo: aceptamos.

> **Cómo llegamos aquí (para contar en el pitch):** la primera versión "predecía" el punto final
> extrapolando las cesiones del proveedor, y cerraba demasiado pronto, porque el proveedor cede
> *en proporción a lo que cedemos nosotros*. Las actas nos lo enseñaron. Cambiamos a medir la
> respuesta por euro y la nota en P01 pasó de **0,68 → 0,75 → 0,998**.

**c) Reglas de cierre:**
- El proveedor dice **agreed** u **oferta final** y ningún otro proveedor puede mejorarlo → aceptar.
- **Último mensaje** permitido → aceptar la mejor oferta válida.
- **Nunca aceptar en su primera respuesta** (una primera cesión no dice dónde para).
- **Nunca aceptar algo que cueste más que la primera oferta** (eso puntúa 0) ni que rompa un
  requisito obligatorio, ni que sea peor que nuestra alternativa.
- Si nada es aceptable → retirarse.

**d) El ritmo de cesión:** cada ronda subimos una fracción de la distancia hasta su contraoferta:
- *mantener* 5 %, *poco* 20 %, *normal* 33 %, *mucho* 50 % (lo elige el Director);
- siempre **al menos un 0,3 %** (sin movimiento nuestro, no hay concesión suya);
- si el proveedor tiene comité de aprobación, **como mucho +1,5 % por ronda**;
- si llevamos demasiada "distancia acumulada" (los proveedores se van hacia el 40 %), aceleramos.

**e) Escuchar en directo:** en cada ronda vuelve a leer los e-mails del proveedor buscando
necesidades nuevas. Si detecta `cash`, sube su "coste del dinero" y el menú pasa a valorar más
pagarle antes. Si el Director no ofrece ningún intercambio, el código añade el que encaja con la
necesidad detectada.

**f) El e-mail:**
- el párrafo del Director, filtrado: se quitan frases con cifras, códigos o palabras peligrosas
  ("budget", "deadline", "urgent", "no alternative"…);
- los argumentos con sus datos;
- las propuestas en palabras ("unit price EUR 130.60, payment 14 days net…");
- la pregunta;
- la firma.

Si la IA falla, se usa una plantilla según el tono.

**g) Robustez:** el estado se **reconstruye a partir del hilo** en cada movimiento. Si el programa
se cae en la final, se reinicia y sigue donde iba.

### 4.6 `minutes.py` · Las actas ⭐ clave para el jurado
Por cada caso se guarda un archivo en `logs/hackit/` con **todo lo que pensó nuestro lado**:
- qué necesidades vio;
- qué dijo cada departamento;
- qué decidió el Director y **por qué** (en una frase);
- los precios que puso el código, la brújula y la respuesta del proveedor por euro;
- por qué aceptó.

El jurado lee las repeticiones de Tacto (el lado del proveedor); **nosotros podemos enseñar el otro
lado**.

### 4.7 `__main__.py` · Los comandos
Ver sección 6.

---

## 5. Resultados de las pruebas (24 oct, primera versión)

| Prueba | Nota | Comentario |
|---|---|---|
| Simulador local S01 / S02 / S03 (sin IA) | 0,87 / 0,86 / 0,74 | La "fontanería" funciona |
| Sandbox D01 (subida de precio) | 0,74 → **0,86** | Tras arreglar el cierre prematuro |
| Sandbox D02 (concurso, 2 proveedores) | 0,62 | Mejorable |
| **P01 fundición +9,5 %** | 0,68 → 0,75 → **0,998** | Bajó de 138,55 € a 130,60 € pagando antes |
| Batería completa práctica (10 casos) | **media 0,49** | Muy desigual, ver abajo |

Detalle de la batería (antes del último arreglo):

| Caso | Nota | Diagnóstico |
|---|---|---|
| P01 | 0,998 | ✅ |
| P11 calidad (Kalwisz) | 0,79 | ✅ bien |
| P04 / P03 / P07 renovaciones | 0,64 / 0,62 / 0,60 | Aceptables, mejorables |
| P18 materia prima | 0,51 | Cláusula de índice mal aprovechada |
| P05 concurso 3 proveedores | 0,30 | Con 3 proveedores el comité no tiene tiempo → solo código |
| P08 materia prima | 0,28 | Cláusula de índice |
| P10 capacidad | 0,18 | Proveedor lleno: cobramos mal plazo y volumen |
| P02 cobre | **0** | Aceptó un trato **más caro que la oferta inicial** (la cláusula de índice lo encarecía). **Ya arreglado**: nunca acepta algo más caro que la primera oferta |

**Referencias para comparar:** el agente propio de Tacto saca ~0,70 de media y los ganadores de
hace dos semanas ~0,78–0,81. Nuestro P01 ya iguala el mejor récord conocido. Los casos flojos
son el trabajo de mañana.

---

## 6. Cómo ejecutarlo

Desde la raíz de este repositorio (con el `.env` con las claves; **nunca se sube a GitHub**):

```bash
uv sync --all-extras                                   # instalar (una vez)
uv run arena data                                      # descargar los datos de Tacto (y el domingo tras las 08:00)
uv run python -m hackit dossiers        # Fase 0: expedientes (repetir el domingo tras las 08:00)
uv run python -m hackit -v play case:D01   # un caso del sandbox (gratis)
uv run python -m hackit -v play case:P01   # un caso de práctica (gasta 1 de 200)
uv run python -m hackit run --set practice --parallel 10   # los 10 de práctica
uv run python -m hackit run --set final --parallel 10    # LA FINAL: arrancar antes de las 13:30 y no tocar
```

Opción útil: `--no-model` juega solo con código, sin IA (para probar sin gastar crédito).

Variables de entorno (en `.env`): `ARENA_URL`, `ARENA_API_KEY`, `ANTHROPIC_API_KEY` y, opcionales,
`HACKIT_MODEL` (por defecto `claude-opus-5-5`) y `HACKIT_EFFORT` (por defecto `low`, para ir rápido).

---

## 7. Qué es nuestro y qué no (importante: que no parezca copiado)

El jurado conoce el repositorio ganador anterior (ProcureCabana). Hay **principios generales** que
cualquier equipo serio comparte y no son "suyos": que el código calcule los números, tener una jugada
de reserva y cerrar antes de que se acabe el tiempo. Lo que es **nuestro**:

| Nosotros | Ellos |
|---|---|
| **Comité espejo** de 4 departamentos que *deciden* con un director | 4 investigadores que solo resumían datos; la decisión era una fórmula fija |
| **Menú a coste igual**: la IA elige entre paquetes equivalentes para nosotros y distintos para el proveedor | Reglas fijas de intercambio |
| **Sensor de respuesta por euro** para saber cuándo el proveedor está en su suelo | Porcentajes fijos de apertura y de cesión ajustados a mano |
| **13 necesidades** detectadas en datos **y en los e-mails en directo** (sirve con proveedores nuevos) | Lista de rasgos solo en los datos |
| **Actas** de cada decisión con su porqué | Registros técnicos |
| Ritmo de cesión elegido por el Director según el proveedor | 30 % fijo por ronda |

**Qué sí usamos de su experiencia:** sus *observaciones* sobre cómo se comportan los bots (por
ejemplo, que "agreed" congela el precio). Son hechos del entorno, igual que leer el reglamento.

---

## 8. Cómo contarlo en el pitch (5 minutos)

**Estructura propuesta:**
1. **(30 s) El gancho:** "El proveedor es un equipo de departamentos. Nosotros mandamos otro equipo."
2. **(60 s) El comité:** diapositiva con los 5 agentes y sus lentes. Frase clave: *"Cada
   departamento defiende su palanca; el director decide. Como en una empresa de verdad."*
3. **(60 s) El menú a coste igual:** el ejemplo de P01: "122,62 € a 14 días nos cuesta lo mismo que
   124,70 € a 90 días, pero al proveedor sin liquidez le vale 1,7 € más por pieza. Ese es un
   intercambio gratis, y lo encontró el código, no la intuición."
4. **(60 s) Una negociación real paso a paso** con las actas:
   - qué pistas vio (*"he works from his own spreadsheet"*, *"pay our invoices within 10 days"*);
   - qué dijo cada departamento;
   - cómo cedió el proveedor;
   - por qué cerramos.
5. **(30 s) El sensor de respuesta y la historia del error:** "Nuestra primera versión cerraba
   demasiado pronto; las actas nos enseñaron por qué; pasamos de 0,68 a 0,998."
6. **(30 s) Resultados y cierre:** puntuación en la final y qué haríamos después.

**Preguntas probables del jurado y respuestas:**
- *"¿Por qué escribió eso tu agente?"* → Abrimos las actas: está la opinión de cada departamento y
  la frase del Director.
- *"¿Funcionaría con un proveedor real?"* → Sí: el comité razona como un departamento de compras de
  verdad (liquidez, capacidad, calidad, mercado) y solo usa datos verificables. Las frases que
  detectan necesidades son el tipo de cosas que un comprador apunta en sus notas.
- *"¿Qué pasa si la IA se equivoca o tarda?"* → No puede poner precios ni aceptar. Si tarda, el
  código decide solo y nunca nos pasamos de 60 s.
- *"¿Qué aprendió tu agente de los proveedores?"* → Que ceden en proporción a lo que cedemos; que la
  liquidez y la capacidad valen más que el precio para algunos; que el primer e-mail con datos es el
  que más mueve.
- *"¿En qué es distinto del ganador anterior?"* → Sección 7.

---

## 9. Debilidades conocidas y siguiente trabajo

1. **Concursos con 3 proveedores (P05):** sin tiempo para el comité. Idea: el comité prepara la
   estrategia en la Fase A, y en cada ronda solo se reúne para el proveedor que va ganando.
2. **Cláusulas de índice (P02, P08, P18):** el menú las ofrece, pero el agente aún no sabe cuándo
   aceptarlas o pedirlas. Hay que valorar mejor cuánto vale la cláusula para un distribuidor.
3. **Capacidad (P10):** con un proveedor lleno, hay que cambiar plazo y compromiso por precio.
4. **Preparar la final:** ensayo con el simulador de rondas (`arena-sim --final-rehearsal`) y
   comprobar que los datos del domingo se cargan.
5. **El motor de precio (lo más urgente):** con nuestra misma clave, en el sandbox, el agente ganador
   anterior saca **0,99 en D01 y 0,69 en D02**, frente a nuestro **0,86 y 0,62**. Cuánto ceder en cada
   ronda y cuándo cerrar es donde más puntos perdemos.
