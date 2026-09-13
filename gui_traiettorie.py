#!/usr/bin/env python3
"""
gui_traiettorie.py
===================

Interfaccia grafica (PySide6 / Qt) per l'analisi della traiettoria di un
oggetto in movimento a partire da un video.

Per il flusso di lavoro, le dipendenze (Python ed esterne) e le istruzioni
di avvio, vedi README.md nella stessa cartella.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from fractions import Fraction

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

FRAME_NAME_RE = re.compile(r"^fotogramma\d+\.png$")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FOTOGRAMMI_ROOT = os.path.join(SCRIPT_DIR, "fotogrammi")


# --------------------------------------------------------------------------
# Funzioni di supporto (wrapper attorno ai comandi esterni) -- indipendenti
# dal toolkit grafico usato, cosi' la logica resta la stessa dello script
# di partenza qualunque sia l'interfaccia costruita sopra.
# --------------------------------------------------------------------------

def which_or_raise(cmd):
    if shutil.which(cmd) is None:
        raise FileNotFoundError(
            f"Il comando '{cmd}' non e' stato trovato nel PATH. "
            f"Installa ffmpeg / imagemagick prima di continuare."
        )


def dolphin_places_urls():
    """Legge il pannello 'Risorse' (Places) di Dolphin, il file manager di
    KDE Plasma, dal file XBEL condiviso da tutte le app KDE:
    $XDG_DATA_HOME/user-places.xbel (di norma ~/.local/share/user-places.xbel).

    Restituisce gli URL nello stesso ordine in cui compaiono in Dolphin,
    escludendo le voci nascoste dall'utente (<IsHidden>true</IsHidden>) e
    le voci che non sono cartelle locali esistenti (dispositivi non
    montati, Cestino, Rete, "File/percorsi recenti", ecc., che usano
    protocolli KIO non risolvibili da una finestra di selezione file Qt
    "semplice")."""
    xbel_path = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "user-places.xbel",
    )
    if not os.path.isfile(xbel_path):
        return []

    try:
        root = ET.parse(xbel_path).getroot()
    except ET.ParseError:
        return []

    urls = []
    for bookmark in root.findall(".//bookmark"):
        href = bookmark.get("href", "")
        if not href.startswith("file:"):
            continue  # scarta trash:/, remote:/, recentlyused:/ e simili
        hidden = bookmark.findtext(
            "./info/metadata[@owner='http://www.kde.org']/IsHidden"
        )
        if hidden and hidden.strip().lower() == "true":
            continue
        url = QUrl(href)
        if os.path.isdir(url.toLocalFile()):
            urls.append(url)
    return urls


def standard_paths_urls():
    """Ripiego multipiattaforma: le cartelle 'speciali' del sistema
    operativo (Home, Scrivania, Documenti, Download, Immagini, Video,
    Musica) tramite QStandardPaths, usato quando il pannello Risorse di
    Dolphin non e' disponibile (KDE non installato/non in uso, o file
    user-places.xbel assente, come su Windows/macOS o altri ambienti
    desktop Linux)."""
    location_types = [
        QStandardPaths.HomeLocation,
        QStandardPaths.DesktopLocation,
        QStandardPaths.DocumentsLocation,
        QStandardPaths.DownloadLocation,
        QStandardPaths.PicturesLocation,
        QStandardPaths.MoviesLocation,
        QStandardPaths.MusicLocation,
    ]
    seen = set()
    urls = []
    for loc in location_types:
        for path in QStandardPaths.standardLocations(loc):
            if path and path not in seen and os.path.isdir(path):
                seen.add(path)
                urls.append(QUrl.fromLocalFile(path))
    return urls


def system_quick_access_urls():
    """Cartelle da mostrare nella barra laterale delle finestre di
    selezione file: se disponibile, la lista di 'accesso rapido' reale di
    Dolphin/KDE (dolphin_places_urls); altrimenti le posizioni standard
    del sistema operativo (standard_paths_urls) come ripiego generico."""
    return dolphin_places_urls() or standard_paths_urls()


def frames_subdir_for_video(video_path, base_root=FOTOGRAMMI_ROOT):
    """Calcola una sottocartella di 'fotogrammi/' dedicata al video scelto,
    diversa per ogni file (anche se due video hanno lo stesso nome ma si
    trovano in percorsi diversi, grazie all'hash del percorso assoluto)."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or "video"
    digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:8]
    return os.path.join(base_root, f"{safe_stem}_{digest}")


def probe_video(path):
    """Interroga ffprobe per ottenere risoluzione, fps e numero di
    fotogrammi stimato del video scelto."""
    which_or_raise("ffprobe")
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames:format=duration",
        "-of", "json", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe ha restituito un errore:\n{result.stderr}")

    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("Nessuno stream video trovato nel file scelto.")
    stream = streams[0]
    fmt = data.get("format", {})

    width = int(stream["width"])
    height = int(stream["height"])

    fps = None
    rate = stream.get("r_frame_rate")
    if rate and rate != "0/0":
        try:
            fps = float(Fraction(rate))
        except (ZeroDivisionError, ValueError):
            fps = None

    nb_frames = stream.get("nb_frames")
    try:
        nb_frames = int(nb_frames)
    except (TypeError, ValueError):
        nb_frames = None

    duration = None
    try:
        if fmt.get("duration") is not None:
            duration = float(fmt["duration"])
    except ValueError:
        duration = None

    if nb_frames is None and duration is not None and fps:
        nb_frames = int(duration * fps)
    if duration is None and nb_frames is not None and fps:
        duration = nb_frames / fps

    return {
        "width": width, "height": height, "fps": fps,
        "nb_frames": nb_frames, "duration": duration,
    }


def format_time(seconds):
    """Formatta un istante in secondi come MM:SS.d per le etichette dei
    cursori dell'intervallo di suddivisione."""
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:04.1f}"


def extract_thumbnail(video_path, time_seconds, out_path, max_size=320):
    """Estrae un singolo fotogramma all'istante indicato (anteprima
    grafica per la scelta dell'intervallo di suddivisione), usando lo
    stesso ffmpeg gia' richiesto per la suddivisione vera e propria."""
    which_or_raise("ffmpeg")
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, time_seconds):.3f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", f"scale='min({max_size},iw)':-2",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Impossibile estrarre l'anteprima a {time_seconds:.1f}s:\n{result.stderr}"
        )


def frame_size_from_png(path):
    """Legge la risoluzione reale di un fotogramma gia' estratto,
    tramite 'identify' di ImageMagick (usato anche per il ricampionamento)."""
    which_or_raise("identify")
    result = subprocess.run(
        ["identify", "-format", "%w %h", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"identify ha restituito un errore:\n{result.stderr}")
    w, h = result.stdout.split()
    return int(w), int(h)


def split_video(video_path, out_dir, sink, cancel_event, start_seconds=None, end_seconds=None):
    """Equivalente di split.sh, con log del progresso in tempo reale.
    'sink' e' un oggetto con un metodo .put((tipo, ...)) - vedi SignalSink.
    start_seconds/end_seconds, se indicati, limitano la suddivisione al
    solo intervallo scelto dall'utente (entrambi come opzioni di INPUT di
    ffmpeg, cosi' i tempi restano quelli assoluti del video originale)."""
    which_or_raise("ffmpeg")
    os.makedirs(out_dir, exist_ok=True)

    # ripulisce eventuali fotogrammi di un'estrazione precedente nella
    # stessa sottocartella, per non lasciare file "orfani"
    for old in glob.glob(os.path.join(out_dir, "fotogramma*.png")):
        os.remove(old)

    out_pattern = os.path.join(out_dir, "fotogramma%03d.png")
    cmd = ["ffmpeg", "-y"]
    if start_seconds:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]
    cmd += ["-i", video_path, out_pattern]
    sink.put(("log", "Comando: " + " ".join(cmd)))

    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    frame_re = re.compile(r"frame=\s*(\d+)")
    for line in proc.stderr:
        if cancel_event.is_set():
            proc.terminate()
            sink.put(("log", "Estrazione interrotta dall'utente."))
            break
        m = frame_re.search(line)
        if m:
            sink.put(("split_progress", int(m.group(1))))
    proc.wait()

    if cancel_event.is_set():
        sink.put(("split_done", False))
        return
    if proc.returncode != 0:
        sink.put(("error", "ffmpeg ha terminato con un errore (vedi log)."))
        sink.put(("split_done", False))
        return

    n = len(glob.glob(os.path.join(out_dir, "fotogramma*.png")))
    sink.put(("log", f"Estrazione completata: {n} fotogrammi in '{out_dir}'."))
    sink.put(("split_done", True))


def combine_frames(frames_dir, step, background, out_path, width, height,
                    sink, cancel_event):
    """Equivalente di combine.pl, generalizzato con step/sfondo scelti
    dall'utente e con inizializzazione esplicita dell'immagine di base
    (in combine.pl 'out.png' doveva gia' esistere: qui viene creata)."""
    which_or_raise("convert")

    bg_color = "white" if background == "bianco" else "black"
    compose = "Darken" if background == "bianco" else "Lighten"

    init_cmd = ["convert", "-size", f"{width}x{height}", f"xc:{bg_color}", out_path]
    sink.put(("log", "Comando: " + " ".join(init_cmd)))
    result = subprocess.run(init_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sink.put(("error", f"Impossibile creare l'immagine di base:\n{result.stderr}"))
        sink.put(("combine_done", False))
        return

    frames = sorted(
        f for f in os.listdir(frames_dir) if FRAME_NAME_RE.match(f)
    )
    selected = [f for i, f in enumerate(frames) if i % step == 0]
    total = len(selected)
    if total == 0:
        sink.put(("error", "Nessun fotogramma selezionato con questo step."))
        sink.put(("combine_done", False))
        return

    sink.put(("log", f"Sovrapposizione di {total}/{len(frames)} fotogrammi "
                      f"(step={step}, sfondo={background}, compose={compose})."))

    for idx, fname in enumerate(selected):
        if cancel_event.is_set():
            sink.put(("log", "Sovrapposizione interrotta dall'utente."))
            sink.put(("combine_done", False))
            return
        fpath = os.path.join(frames_dir, fname)
        cmd = ["convert", out_path, fpath, "-compose", compose, "-composite", out_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            sink.put(("error", f"Errore su '{fname}':\n{result.stderr}"))
            sink.put(("combine_done", False))
            return
        sink.put(("combine_progress", idx + 1, total))

    sink.put(("log", f"Immagine di traiettoria salvata in '{out_path}'."))
    sink.put(("combine_done", True))


# --------------------------------------------------------------------------
# Adattatore thread -> segnali Qt
# --------------------------------------------------------------------------

class SignalSink(QObject):
    """Espone un metodo .put(tuple) chiamabile da un thread worker e lo
    inoltra come segnale Qt: le connessioni verso slot che vivono nel
    thread della GUI vengono automaticamente accodate da Qt (queued
    connection), quindi e' sicuro aggiornare i widget dagli slot."""

    log = Signal(str)
    error = Signal(str)
    split_progress = Signal(int)
    split_done = Signal(bool)
    combine_progress = Signal(int, int)
    combine_done = Signal(bool)

    def put(self, item):
        kind, *rest = item
        getattr(self, kind).emit(*rest)


class ThumbnailSignal(QObject):
    """Segnali per riportare alla GUI, dal thread worker, l'esito
    dell'estrazione di un'anteprima (vedi extract_thumbnail). 'seq' e' un
    numero di sequenza: la GUI lo confronta con l'ultima richiesta fatta
    per scartare risultati arrivati fuori ordine (es. l'utente ha
    trascinato il cursore piu' volte prima che l'anteprima precedente
    fosse pronta)."""

    ready = Signal(str, int, str)   # which ("start"/"end"), seq, percorso PNG
    failed = Signal(str, int, str)  # which, seq, messaggio di errore


class ThumbnailLabel(QLabel):
    """QLabel per le anteprime di inizio/fine intervallo che ricalcola il
    fotogramma mostrato ogni volta che il riquadro cambia dimensione (per
    esempio quando l'utente ridimensiona la finestra), invece di restare
    fissato alla risoluzione con cui l'anteprima e' stata caricata. Tiene
    una copia del fotogramma originale e ne rigenera una versione scalata
    (mantenendo le proporzioni) alla dimensione attuale del riquadro."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_pixmap = None
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAlignment(Qt.AlignCenter)

    def setPixmap(self, pixmap):
        self._original_pixmap = pixmap
        self._rescale()

    def setText(self, text):
        self._original_pixmap = None
        super().setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        if self._original_pixmap is None or self._original_pixmap.isNull():
            return
        super().setPixmap(self._original_pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class TraiettorieWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Traiettorie - analisi cinematica da video")

        self.video_path = None
        self.video_info = None      # dict con width/height/fps/nb_frames/duration
        self.frames_dir = None
        self.out_image_path = None
        self.cancel_event = threading.Event()
        self._expected_split_frames = None

        # anteprime dell'intervallo di suddivisione (grafiche, vedi
        # _build_range_box): cartella temporanea per i fotogrammi estratti
        # al volo e segnali per riceverli dal thread di estrazione
        self._thumb_dir = tempfile.mkdtemp(prefix="traiettorie_thumbs_")
        self._thumb_seq = {"start": 0, "end": 0}
        self._thumb_signal = ThumbnailSignal()
        self._thumb_signal.ready.connect(self._on_thumbnail_ready)
        self._thumb_signal.failed.connect(self._on_thumbnail_failed)
        self._start_thumb_timer = QTimer(self)
        self._start_thumb_timer.setSingleShot(True)
        self._start_thumb_timer.timeout.connect(lambda: self._request_thumbnail("start"))
        self._end_thumb_timer = QTimer(self)
        self._end_thumb_timer.setSingleShot(True)
        self._end_thumb_timer.timeout.connect(lambda: self._request_thumbnail("end"))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_video_box())
        # l'intervallo di suddivisione riceve tutto lo spazio verticale in
        # eccesso quando la finestra viene ingrandita, cosi' i riquadri di
        # anteprima possono crescere invece delle altre sezioni
        root.addWidget(self._build_range_box(), stretch=1)
        root.addWidget(self._build_split_box())
        root.addWidget(self._build_combine_box())
        root.addWidget(self._build_preview_box())
        root.addWidget(self._build_log_box())

    def closeEvent(self, event):
        shutil.rmtree(self._thumb_dir, ignore_errors=True)
        super().closeEvent(event)

    # -- costruzione interfaccia -----------------------------------------

    def _build_video_box(self):
        box = QGroupBox("1. Video sorgente")
        layout = QGridLayout(box)

        self.video_edit = QLineEdit()
        self.video_edit.setReadOnly(True)
        self.video_edit.setPlaceholderText("Nessun video selezionato.")
        browse_btn = QPushButton("Scegli video...")
        browse_btn.clicked.connect(self.scegli_video)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: gray;")

        layout.addWidget(self.video_edit, 0, 0)
        layout.addWidget(browse_btn, 0, 1)
        layout.addWidget(self.info_label, 1, 0, 1, 2)
        return box

    def _build_range_box(self):
        box = QGroupBox("2. Intervallo di suddivisione")
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(box)

        hint = QLabel(
            "Trascina i due cursori per scegliere da quale istante a quale "
            "istante del video estrarre i fotogrammi; l'anteprima mostra il "
            "fotogramma corrispondente alla posizione scelta."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        outer.addWidget(hint)

        cols = QHBoxLayout()
        self.start_thumb, self.start_slider, self.start_time_label = \
            self._build_range_column(cols, "Inizio")
        self.end_thumb, self.end_slider, self.end_time_label = \
            self._build_range_column(cols, "Fine")
        outer.addLayout(cols, stretch=1)

        self.start_slider.valueChanged.connect(self._on_start_slider_changed)
        self.end_slider.valueChanged.connect(self._on_end_slider_changed)
        return box

    def _build_range_column(self, parent_layout, title):
        col = QVBoxLayout()
        col.addWidget(QLabel(f"<b>{title}</b>"), alignment=Qt.AlignHCenter)

        thumb = ThumbnailLabel("(seleziona un video)")
        thumb.setStyleSheet("background: #202020; color: gray; border: 1px solid gray;")
        col.addWidget(thumb, stretch=1)

        slider = QSlider(Qt.Horizontal)
        slider.setEnabled(False)
        col.addWidget(slider)

        time_label = QLabel(f"{title}: --:--.-")
        col.addWidget(time_label, alignment=Qt.AlignHCenter)

        parent_layout.addLayout(col)
        return thumb, slider, time_label

    def _build_split_box(self):
        box = QGroupBox("3. Suddivisione in fotogrammi")
        layout = QGridLayout(box)

        self.frames_dir_edit = QLineEdit()
        change_dir_btn = QPushButton("Cambia cartella...")
        change_dir_btn.clicked.connect(self.scegli_cartella_fotogrammi)

        self.split_btn = QPushButton("Suddividi in fotogrammi")
        self.split_btn.setEnabled(False)
        self.split_btn.clicked.connect(self.avvia_split)

        self.split_progress = QProgressBar()
        self.split_progress.setRange(0, 100)
        self.split_status = QLabel("")
        self.split_status.setStyleSheet("color: gray;")

        layout.addWidget(self.frames_dir_edit, 0, 0)
        layout.addWidget(change_dir_btn, 0, 1)
        layout.addWidget(self.split_btn, 1, 0)
        layout.addWidget(self.split_progress, 1, 1)
        layout.addWidget(self.split_status, 2, 0, 1, 2)
        return box

    def _build_combine_box(self):
        box = QGroupBox("4. Step di sovrapposizione e sfondo")
        layout = QGridLayout(box)

        layout.addWidget(QLabel("Sovrapponi 1 fotogramma ogni:"), 0, 0)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 999)
        self.step_spin.setValue(2)
        layout.addWidget(self.step_spin, 0, 1)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("Sfondo del filmato:"))
        self.bg_bianco = QRadioButton("Bianco (oggetto scuro)")
        self.bg_nero = QRadioButton("Nero (oggetto chiaro)")
        self.bg_bianco.setChecked(True)
        self.bg_group = QButtonGroup(self)
        self.bg_group.addButton(self.bg_bianco)
        self.bg_group.addButton(self.bg_nero)
        bg_row.addWidget(self.bg_bianco)
        bg_row.addWidget(self.bg_nero)
        bg_row.addStretch(1)
        layout.addLayout(bg_row, 1, 0, 1, 2)

        self.combine_btn = QPushButton("Genera immagine traiettoria")
        self.combine_btn.setEnabled(False)
        self.combine_btn.clicked.connect(self.avvia_combine)
        self.combine_progress = QProgressBar()
        self.combine_progress.setRange(0, 100)

        layout.addWidget(self.combine_btn, 2, 0)
        layout.addWidget(self.combine_progress, 2, 1)
        return box

    def _build_preview_box(self):
        box = QGroupBox("Anteprima traiettoria")
        layout = QVBoxLayout(box)

        self.preview_label = QLabel("(nessuna immagine generata)")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(200)

        self.save_btn = QPushButton("Salva immagine con nome...")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.salva_immagine)

        layout.addWidget(self.preview_label)
        layout.addWidget(self.save_btn, alignment=Qt.AlignLeft)
        return box

    def _build_log_box(self):
        box = QGroupBox("Log")
        layout = QVBoxLayout(box)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(160)
        layout.addWidget(self.log_text)
        return box

    # -- azione: scelta video ---------------------------------------------

    def scegli_video(self):
        dialog = QFileDialog(self, "Scegli il video da analizzare")
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setNameFilter(
            "Video (*.avi *.mp4 *.mov *.mkv *.mpg *.mpeg *.wmv *.flv);;Tutti i file (*)"
        )
        self._apply_quick_access(dialog)
        if dialog.exec() != QFileDialog.Accepted or not dialog.selectedFiles():
            return
        path = dialog.selectedFiles()[0]
        try:
            info = probe_video(path)
        except Exception as exc:
            QMessageBox.critical(self, "Errore", str(exc))
            return

        self.video_path = path
        self.video_info = info
        self.video_edit.setText(path)

        fps_txt = f"{info['fps']:.2f}" if info["fps"] else "n/d"
        nfr_txt = str(info["nb_frames"]) if info["nb_frames"] else "n/d"
        self.info_label.setText(
            f"Risoluzione: {info['width']}x{info['height']}  |  "
            f"fps: {fps_txt}  |  fotogrammi stimati: {nfr_txt}"
        )

        # cartella fotogrammi di default: fotogrammi/<nome-video>_<hash>/,
        # diversa per ogni file video (anche omonimi in cartelle diverse)
        default_dir = frames_subdir_for_video(path)
        self.frames_dir_edit.setText(default_dir)

        self._setup_range_controls(info)

        self.split_progress.setValue(0)
        self.split_status.setText("")
        self.combine_btn.setEnabled(False)
        self.split_btn.setEnabled(True)
        self._log(f"Video selezionato: {path}")
        self._log(f"Cartella fotogrammi proposta: {default_dir}")

    def _setup_range_controls(self, info):
        """Configura i due cursori dell'intervallo di suddivisione in base
        alla durata del video appena selezionato (risoluzione di un
        decimo di secondo) e richiede subito le due anteprime iniziali
        (inizio e fine video)."""
        duration = info.get("duration")
        for slider in (self.start_slider, self.end_slider):
            slider.blockSignals(True)
        if duration and duration > 0:
            max_ds = max(1, round(duration * 10))  # risoluzione: decisecondi
            self.start_slider.setRange(0, max_ds)
            self.start_slider.setValue(0)
            self.end_slider.setRange(0, max_ds)
            self.end_slider.setValue(max_ds)
            self.start_slider.setEnabled(True)
            self.end_slider.setEnabled(True)
        else:
            self.start_slider.setRange(0, 0)
            self.end_slider.setRange(0, 0)
            self.start_slider.setEnabled(False)
            self.end_slider.setEnabled(False)
            self._log("Durata del video non determinabile: verra' suddiviso per intero.")
        for slider in (self.start_slider, self.end_slider):
            slider.blockSignals(False)

        self.start_time_label.setText(f"Inizio: {format_time(0.0)}")
        self.end_time_label.setText(f"Fine: {format_time(duration or 0.0)}")
        for thumb in (self.start_thumb, self.end_thumb):
            thumb.setText("(caricamento...)" if duration else "(non disponibile)")

        if duration and duration > 0:
            self._request_thumbnail("start")
            self._request_thumbnail("end")

    def scegli_cartella_fotogrammi(self):
        start_dir = FOTOGRAMMI_ROOT if os.path.isdir(FOTOGRAMMI_ROOT) else SCRIPT_DIR
        dialog = QFileDialog(self, "Cartella dove salvare i fotogrammi", start_dir)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        self._apply_quick_access(dialog)
        if dialog.exec() == QFileDialog.Accepted and dialog.selectedFiles():
            self.frames_dir_edit.setText(dialog.selectedFiles()[0])

    # -- selezione grafica dell'intervallo di suddivisione -------------------

    def _on_start_slider_changed(self, value):
        # l'inizio non puo' raggiungere o superare la fine
        if value >= self.end_slider.value():
            value = max(self.start_slider.minimum(), self.end_slider.value() - 1)
            self.start_slider.blockSignals(True)
            self.start_slider.setValue(value)
            self.start_slider.blockSignals(False)
        self.start_time_label.setText(f"Inizio: {format_time(value / 10.0)}")
        self._start_thumb_timer.start(150)  # debounce: aspetta che il trascinamento si fermi

    def _on_end_slider_changed(self, value):
        # la fine non puo' raggiungere o precedere l'inizio
        if value <= self.start_slider.value():
            value = min(self.end_slider.maximum(), self.start_slider.value() + 1)
            self.end_slider.blockSignals(True)
            self.end_slider.setValue(value)
            self.end_slider.blockSignals(False)
        self.end_time_label.setText(f"Fine: {format_time(value / 10.0)}")
        self._end_thumb_timer.start(150)

    def _request_thumbnail(self, which):
        if not self.video_path:
            return
        slider = self.start_slider if which == "start" else self.end_slider
        self._thumb_seq[which] += 1
        seq = self._thumb_seq[which]
        seconds = slider.value() / 10.0
        # l'ultimo istante del video spesso non e' un fotogramma raggiungibile
        # (l'ultimo frame reale ha un timestamp leggermente inferiore alla
        # durata dichiarata): per l'anteprima si arretra di un margine di
        # sicurezza, senza toccare il valore usato per la suddivisione vera
        duration = self.video_info.get("duration") if self.video_info else None
        if duration:
            seconds = min(seconds, max(0.0, duration - 0.15))
        out_path = os.path.join(self._thumb_dir, f"{which}_{seq}.png")
        threading.Thread(
            target=self._thumbnail_worker,
            args=(which, seq, seconds, out_path),
            daemon=True,
        ).start()

    def _thumbnail_worker(self, which, seq, seconds, out_path):
        try:
            extract_thumbnail(self.video_path, seconds, out_path)
        except Exception as exc:
            self._thumb_signal.failed.emit(which, seq, str(exc))
            return
        self._thumb_signal.ready.emit(which, seq, out_path)

    def _on_thumbnail_ready(self, which, seq, path):
        # scarta i risultati non piu' aggiornati (es. il cursore e' stato
        # trascinato di nuovo prima che questa anteprima fosse pronta)
        if seq != self._thumb_seq[which]:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        label = self.start_thumb if which == "start" else self.end_thumb
        pixmap = QPixmap(path)
        try:
            os.remove(path)
        except OSError:
            pass
        if pixmap.isNull():
            label.setText("(anteprima non disponibile)")
            return
        label.setText("")
        label.setPixmap(pixmap.scaled(
            label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def _on_thumbnail_failed(self, which, seq, msg):
        if seq != self._thumb_seq[which]:
            return
        label = self.start_thumb if which == "start" else self.end_thumb
        label.setText("(anteprima non disponibile)")
        self._log(f"Anteprima '{which}' non disponibile: {msg}")

    # -- azione: split -----------------------------------------------------

    def avvia_split(self):
        if not self.video_path:
            return
        out_dir = self.frames_dir_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "Attenzione", "Specifica una cartella per i fotogrammi.")
            return

        self.frames_dir = out_dir
        self.split_progress.setValue(0)
        self.split_status.setText("Estrazione in corso...")

        start_seconds = end_seconds = None
        if self.start_slider.isEnabled():
            start_seconds = self.start_slider.value() / 10.0
            end_seconds = self.end_slider.value() / 10.0
            self._log(f"Intervallo scelto: da {format_time(start_seconds)} "
                      f"a {format_time(end_seconds)}.")

        # stima dei fotogrammi attesi nell'intervallo scelto, per la barra
        # di avanzamento (altrimenti si ripiega sulla stima sull'intero video)
        fps = self.video_info.get("fps") if self.video_info else None
        if fps and end_seconds is not None:
            self._expected_split_frames = max(1, round((end_seconds - (start_seconds or 0.0)) * fps))
        else:
            self._expected_split_frames = self.video_info.get("nb_frames") if self.video_info else None

        self.cancel_event.clear()
        self._set_busy(True)
        self._log("Avvio suddivisione in fotogrammi...")

        sink = SignalSink()
        sink.log.connect(self._log)
        sink.error.connect(self._on_error)
        sink.split_progress.connect(self._on_split_progress)
        sink.split_done.connect(self._on_split_done)
        self._split_sink = sink  # tiene un riferimento forte finche' il thread e' vivo

        threading.Thread(
            target=split_video,
            args=(self.video_path, out_dir, sink, self.cancel_event, start_seconds, end_seconds),
            daemon=True,
        ).start()

    # -- azione: combine -----------------------------------------------------

    def avvia_combine(self):
        if not self.frames_dir or not os.path.isdir(self.frames_dir):
            QMessageBox.warning(self, "Attenzione", "Suddividi prima il video in fotogrammi.")
            return
        frames = sorted(f for f in os.listdir(self.frames_dir) if FRAME_NAME_RE.match(f))
        if not frames:
            QMessageBox.warning(self, "Attenzione", "Nessun fotogramma trovato nella cartella scelta.")
            return

        step = self.step_spin.value()

        try:
            width, height = frame_size_from_png(os.path.join(self.frames_dir, frames[0]))
        except Exception as exc:
            QMessageBox.critical(self, "Errore", str(exc))
            return

        background = "bianco" if self.bg_bianco.isChecked() else "nero"
        out_path = os.path.join(self.frames_dir, "out.png")
        self.out_image_path = out_path

        self.combine_progress.setValue(0)
        self.cancel_event.clear()
        self._set_busy(True)
        self._log("Avvio sovrapposizione fotogrammi...")

        sink = SignalSink()
        sink.log.connect(self._log)
        sink.error.connect(self._on_error)
        sink.combine_progress.connect(self._on_combine_progress)
        sink.combine_done.connect(self._on_combine_done)
        self._combine_sink = sink

        threading.Thread(
            target=combine_frames,
            args=(self.frames_dir, step, background, out_path, width, height,
                  sink, self.cancel_event),
            daemon=True,
        ).start()

    # -- salvataggio immagine finale ----------------------------------------

    def salva_immagine(self):
        if not self.out_image_path or not os.path.isfile(self.out_image_path):
            return
        dialog = QFileDialog(self, "Salva immagine traiettoria con nome")
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setFileMode(QFileDialog.AnyFile)
        dialog.setNameFilter("Immagine PNG (*.png)")
        dialog.setDefaultSuffix("png")
        dialog.selectFile("traiettoria.png")
        self._apply_quick_access(dialog)
        if dialog.exec() == QFileDialog.Accepted and dialog.selectedFiles():
            dest = dialog.selectedFiles()[0]
            shutil.copyfile(self.out_image_path, dest)
            self._log(f"Immagine copiata in '{dest}'.")

    def _apply_quick_access(self, dialog):
        """Forza l'uso della finestra di selezione file non nativa di Qt e
        ne imposta la barra laterale sulle directory di accesso rapido
        del sistema operativo (vedi system_quick_access_urls): la barra
        laterale personalizzata ha effetto solo sulle finestre non native."""
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setSidebarUrls(system_quick_access_urls())

    # -- slot collegati ai segnali dei worker thread -------------------------

    def _on_error(self, msg):
        self._log("ERRORE: " + msg)
        QMessageBox.critical(self, "Errore", msg)

    def _on_split_progress(self, n):
        total = getattr(self, "_expected_split_frames", None)
        if total:
            self.split_progress.setValue(min(100, int(100 * n / total)))
        self.split_status.setText(f"{n} fotogrammi estratti...")

    def _on_split_done(self, ok):
        self._set_busy(False)
        if ok:
            self.split_progress.setValue(100)
            self.combine_btn.setEnabled(True)
            self.split_status.setText("Estrazione completata.")
        else:
            self.split_status.setText("Estrazione interrotta o non riuscita.")

    def _on_combine_progress(self, done, total):
        self.combine_progress.setValue(int(100 * done / total) if total else 0)

    def _on_combine_done(self, ok):
        self._set_busy(False)
        self.combine_btn.setEnabled(True)
        if ok and self.out_image_path:
            self.combine_progress.setValue(100)
            self._show_preview(self.out_image_path)

    # -- utilita' GUI --------------------------------------------------------

    def _set_busy(self, busy):
        self.split_btn.setEnabled(not busy and bool(self.video_path))
        self.combine_btn.setEnabled(not busy and self.combine_btn.isEnabled())

    def _log(self, msg):
        self.log_text.appendPlainText(msg)

    def _show_preview(self, path):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._log(f"Impossibile mostrare l'anteprima di '{path}'.")
            return
        scaled = pixmap.scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)
        self.save_btn.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    window = TraiettorieWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
