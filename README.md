# Traiettorie

Codice per l'analisi di traiettorie da video: a partire da un filmato di un
oggetto in movimento (es. un esperimento di cinematica), il video viene
scomposto in fotogrammi e i fotogrammi vengono poi sovrapposti in un'unica
immagine "stroboscopica", utile per misurare la traiettoria dell'oggetto.

## File del progetto

| File | Ruolo |
|---|---|
| `video/` | Cartella con i video sorgente |
| `fotogrammi/` | Cartella con i fotogrammi estratti (una sottocartella per ogni video analizzato) |

La GUI rende più semplice l'analisi dei video rispetto ai vecchi script i cui comandi fondamentali sono riportati qui:

- `split.sh` &rarr; `ffmpeg -i VIDEO fotogrammi/fotogramma%03d.png`
- `combine.pl` &rarr; `convert out.png FRAME -compose Darken -composite out.png`

## Dipendenze

**Python:** solo [PySide6](https://pypi.org/project/PySide6/), dichiarata in
[pyproject.toml](pyproject.toml) e installata da [uv](https://docs.astral.sh/uv/)
nel virtualenv del progetto (`.venv/`).

**Programmi da riga di comando (non Python):**

- `ffmpeg` / `ffprobe` &mdash; suddivisione del video in fotogrammi e rilevamento di risoluzione/durata/fps
- ImageMagick (`convert` / `identify`) &mdash; sovrapposizione dei fotogrammi e lettura della loro risoluzione

Vanno installati a parte con il gestore pacchetti del sistema operativo (es.
`apt install ffmpeg imagemagick` su Debian/Ubuntu); non sono gestiti da `uv`.

## Preparazione dell'ambiente

```bash
uv sync
```

Crea (o aggiorna) il virtualenv del progetto in `.venv/` installando PySide6.

## Avvio

```bash
uv run gui_traiettorie.py
```

## Flusso di lavoro della GUI

1. **Scelta del video** &mdash; si seleziona il file video da analizzare; la
   risoluzione, i fps e la durata vengono rilevati automaticamente tramite
   `ffprobe`. Le finestre di selezione file mostrano come barra laterale le
   risorse di accesso rapido di Dolphin (KDE Plasma), se disponibili
   (`~/.local/share/user-places.xbel`), altrimenti le cartelle standard del
   sistema operativo (Home, Scrivania, Documenti, Download, ecc. via
   `QStandardPaths`).
2. **Intervallo di suddivisione** &mdash; due cursori grafici permettono di
   scegliere da quale istante a quale istante del video estrarre i
   fotogrammi; ogni cursore mostra un'anteprima del fotogramma
   corrispondente alla posizione scelta, aggiornata dal vivo mentre lo si
   trascina.
3. **Suddivisione in fotogrammi** &mdash; il video (o solo l'intervallo
   scelto) viene scomposto in fotogrammi PNG con `ffmpeg`, salvati in una
   sottocartella dedicata `fotogrammi/<nome-video>_<hash>/`: ogni video
   analizzato ha la propria sottocartella, cosi' non si mescolano mai
   fotogrammi di video diversi.
4. **Step di sovrapposizione e sfondo** &mdash; si sceglie ogni quanti
   fotogrammi sovrapporne uno (come la variabile `$step` di `combine.pl`) e
   il colore di sfondo del filmato:
   - sfondo **bianco** (oggetto scuro) &rarr; composizione `Darken` (tiene il
     pixel piu' scuro tra i fotogrammi)
   - sfondo **nero** (oggetto chiaro) &rarr; composizione `Lighten` (tiene il
     pixel piu' chiaro tra i fotogrammi)

   Il risultato è un'unica immagine con le posizioni successive
   dell'oggetto sovrapposte, pronta per la misura della traiettoria, con
   anteprima a schermo e possibilità di salvarla con un nome a scelta.

   Al salvataggio, accanto all'immagine (es. `traiettoria.png`) viene
   scritto anche un file di testo omonimo (`traiettoria.txt`) con i
   metadati del video (risoluzione, fps, durata), l'intervallo suddiviso,
   lo step di sovrapposizione usato e l'intervallo temporale tra due
   immagini consecutive della foto stroboscopica (step diviso i fps del
   video) &mdash; utile per la successiva analisi cinematica.
