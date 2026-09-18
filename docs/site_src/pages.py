"""Content for the CanLab documentation site.

Each entry is (filename, title, lede, body_html). The body uses the helpers
passed in from build_site so headings get stable ids for the table of contents.
"""


def build_pages(*, h2, table, video_card, parts, repo):
    pages = []

    # ══ Overview ═══════════════════════════════════════════════════════════
    videos = "".join(video_card(slug, title, length, blurb)
                     for slug, title, length, blurb in parts)
    pages.append((
        "index.html",
        "CanLab",
        "A desktop workstation for reverse-engineering a CAN bus: load a "
        "capture, find what the bytes mean, and export a DBC.",
        f"""
<div class="hero">
  <h1>CanLab</h1>
  <p class="lede">A desktop workstation for reverse-engineering a CAN bus.
     Load a capture, work out which bytes carry what, write the signal
     definitions down, check them against real frames, and export a DBC that
     other tools can read.</p>
  <div class="cta">
    <a class="btn btn-primary" href="install.html">Install</a>
    <a class="btn" href="guide.html">How to use it</a>
    <a class="btn" href="{repo}">Source</a>
  </div>
</div>

{h2("Watch it work")}
<div class="video-card video-feature">
  <div class="yt-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/nbDBaClN8T8?rel=0"
            title="CanLab: Reverse-Engineering a CAN Bus from Real Recordings (Guided Tour)"
            loading="lazy" referrerpolicy="strict-origin-when-cross-origin"
            allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowfullscreen></iframe>
  </div>
  <div class="meta">
    <h3>The guided tour</h3>
    <p class="len">6:06</p>
    <p>One pass through the whole tool against two real recordings. Start
       here. <a href="https://www.youtube.com/watch?v=nbDBaClN8T8">Open on
       YouTube</a>, or <a href="canlab-tour.mp4">download the MP4</a> with
       <a href="canlab-tour.srt">subtitles</a>.</p>
  </div>
</div>
<p>For each beat the frame pushes in on the control being described, dims the
   rest, rings it and captions it, then pulls back out. The rectangle is the
   widget's own geometry, read off the live window at record time, so a control
   that moves in a later build takes its callout with it.</p>
<p>It is a real analysis. The first capture turns out to be a marine NMEA 2000
   bus rather than the J1939 that 29-bit identifiers usually suggest. The
   counter detector finds the sequence byte the specification defines without
   being told the protocol. A wind speed signal is defined, and the same two
   bytes are plotted both ways round: little-endian reads 0.72 to 0.87 m/s,
   big-endian claims 184 to 223.</p>

{h2("The full walkthrough")}
<p>Every tab, in four parts, recorded from the running application with
   captions on by default.</p>
<div class="video-grid">{videos}</div>
<p>All the videos are generated rather than hand-recorded. The recorders drive
   a real main window under Qt's offscreen platform and call the same slots the
   buttons call, so a scene that stops working fails the run instead of quietly
   recording a stale screen.</p>

{h2("What it is for")}
<p>You have a capture from a vehicle bus and a few thousand frames of hex. The
   job is to work out which arbitration IDs matter, which bytes inside them
   move, which of those are actually signals rather than counters and
   checksums, what physical quantity each one represents, and then to write
   that down in a form other tools accept. CanLab is built around that loop.</p>
<div class="cards">
  <a class="card" href="guide.html"><h3>The workflow</h3><p>Load, narrow down,
     define, verify, export. Start here if you have a capture and no idea what
     is in it.</p></a>
  <a class="card" href="tabs.html"><h3>All 16 tabs</h3><p>What each one does
     and when you would reach for it.</p></a>
  <a class="card" href="analysis.html"><h3>How the analysis works</h3>
     <p>Counters, checksums, entropy, correlation, and what the confidence
     numbers do and do not mean.</p></a>
  <a class="card" href="safety.html"><h3>The transmit gate</h3><p>What ARM TX
     covers, what it does not, and why that distinction matters.</p></a>
</div>

{h2("What it is not")}
<p>It is not a signal identifier. The analysis produces candidates ranked by
   heuristics, and a confidence figure is a match fraction over the frames you
   loaded, not a proof. Every result needs verifying against the vehicle before
   you rely on it. The project is a single-author effort, in beta, and has
   not been validated across a wide range of real vehicles. The
   <a href="reference.html#limitations">limitations</a> are listed plainly.</p>

<div class="danger">
  <span class="callout-title">Before you connect to anything</span>
  <p>CanLab can transmit on a CAN bus. Use it only on isolated bench setups: a
     benchtop ECU, <code>vcan0</code>, or dedicated lab hardware. Injecting or
     forwarding frames on a live vehicle bus can interfere with braking,
     steering and airbag systems. Read <a href="safety.html">Safety</a>
     first.</p>
</div>

{h2("Try it without hardware")}
<p>A sample capture ships with the source at
   <code>canlab/sample_data/sample_kona_drive.csv</code>: 6,610 frames across
   10 arbitration IDs over 10 seconds, at rates from 1 Hz to 100 Hz. Every
   offline feature works on it, so you can go through the whole
   <a href="guide.html">workflow</a> before touching a vehicle.</p>
"""))

    # ══ Install ════════════════════════════════════════════════════════════
    pages.append((
        "install.html",
        "Install",
        "Run CanLab from source on Linux, macOS or Windows, or take the "
        "prebuilt Linux binary.",
        f"""
<h1>Install</h1>
<p class="lede">Two ways in: run from source, which works everywhere and is
   what the project is developed against, or take the prebuilt Linux binary if
   you just want to look at it.</p>

{h2("From source")}
<p>This is the supported path.</p>
<pre><code>git clone {repo}.git
cd CanLab
python3 -m venv .venv &amp;&amp; source .venv/bin/activate
pip install -r requirements.txt

cd canlab            # the source root: imports are relative to here
python3 main.py</code></pre>
<p>Python 3.11 or newer. The project is developed and tested on 3.12.</p>
<div class="note">
  <span class="callout-title">Note the <code>cd canlab</code></span>
  <p>On <code>main</code> the application is run as a script from inside the
     package directory, not installed as a module. Running
     <code>python3 canlab/main.py</code> from the repository root will not find
     its imports.</p>
</div>

{h2("Prebuilt Linux binary")}
<p>Each release carries a self-contained x86_64 tarball that needs no Python
   installation:</p>
<pre><code>tar -xzf CanLab-2.0.0-linux-x86_64.tar.gz
cd CanLab/
./CanLab</code></pre>
<p>Grab it from the <a href="{repo}/releases/latest">latest release</a>. It is
   unsigned, and it is built from <code>main</code> on x86_64. There is no
   macOS or Windows binary; run from source on those.</p>

{h2("Optional pieces")}
<p>The application works fully offline with none of these. Each unlocks one
   feature and is inert until you use it.</p>
{table(["What", "Install", "Needed for"], [
    ["AI providers",
     "<code>pip install anthropic groq</code>",
     "The AI ENGINE tab. Or run a local Ollama server, which needs no package "
     "and no key."],
    ["MDF4 logs", "<code>pip install asammdf</code>",
     "Opening <code>.mf4</code> and <code>.mdf</code> captures from CANedge "
     "and similar loggers."],
    ["openpilot logs", "<code>pip install pycapnp</code> plus the cereal "
     "<code>log.capnp</code> schema",
     "Opening <code>.rlog</code> and <code>.qlog</code>. Without both it "
     "raises a clear error rather than guessing."],
    ["Vision OCR",
     "<code>pip install opencv-python rapidocr onnxruntime</code>",
     "Reading a reference value off a dashboard video for calibration. These "
     "are large; skip unless you need it."],
    ["MCP server", "<code>pip install mcp</code>",
     "Exposing the analysis as tools to an MCP client."],
    ["Panda", "<code>pip install pandacan</code>",
     "Using a comma.ai Panda as the interface."],
])}

{h2("API keys")}
<p>Keys for the AI providers go in <strong>Settings &rarr; API KEYS</strong>
   and are stored in the operating system keyring, not in the repository. They
   can also come from the environment:</p>
<pre><code>export ANTHROPIC_API_KEY="sk-ant-..."
export GROQ_API_KEY="gsk_..."</code></pre>
<p>A local Ollama server needs no key. Nothing is sent anywhere until you enter
   a key and ask for an analysis; see
   <a href="integrations.html#ai-engine">the AI engine</a> for exactly what
   leaves the machine.</p>

{h2("Hardware interfaces")}
<p>Live capture goes through
   <a href="https://python-can.readthedocs.io">python-can</a>, so anything it
   supports works: <code>socketcan</code>, <code>slcan</code>,
   <code>gs_usb</code>, <code>pcan</code>, <code>kvaser</code>,
   <code>vector</code>, <code>virtual</code> and the rest, plus
   <code>gvret</code>, which CanLab adds itself for SavvyCAN's hardware. Add
   adapters in <strong>Settings &rarr; CAN ADAPTERS</strong>, which can detect
   what is plugged in and test it without transmitting, then pick one from the
   toolbar. See <a href="integrations.html#hardware-adapters">hardware
   adapters</a>.</p>
<p>On Linux you can practise with no hardware at all using a virtual bus:</p>
<pre><code>sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
cangen vcan0 -g 5          # optional: generate traffic</code></pre>
<p>Then add an adapter on <code>socketcan</code> / <code>vcan0</code> and press
   Connect.
   The <a href="tabs.html#gateway">gateway</a> is the one feature that needs
   two hardware channels.</p>

{h2("System requirements")}
<ul>
  <li>Linux, macOS or Windows with Python 3.11 or newer</li>
  <li>4 GB RAM; 8 GB is more comfortable for the machine-learning features</li>
  <li>A desktop session. The application is a Qt GUI, though its analysis
      modules are importable headless and the test suite runs offscreen.</li>
</ul>

{h2("If it will not start")}
{table(["Symptom", "Cause and fix"], [
    ["<code>ImportError: libEGL.so.1</code> or similar on Linux",
     "Qt needs system graphics libraries that a minimal install lacks. On "
     "Debian or Ubuntu: <code>sudo apt install libegl1 libgl1 libxkbcommon0 "
     "libdbus-1-3</code>."],
    ["<code>ImportError: libpulse.so.0</code>",
     "Qt Multimedia, used by the timeline's video sync, needs the audio "
     "stack. <code>sudo apt install libpulse0</code>."],
    ["<code>ModuleNotFoundError: core</code>",
     "You are running from the wrong directory. <code>cd canlab</code> first, "
     "then <code>python3 main.py</code>."],
    ["Nothing decodes, and the signal table stays empty",
     "Check the cantools version. The project pins a version it is tested "
     "against in <code>requirements.txt</code>; a mismatched one used to fail "
     "silently."],
])}
"""))

    # ══ Safety ═════════════════════════════════════════════════════════════
    pages.append((
        "safety.html",
        "Safety",
        "What the ARM TX gate covers, what it deliberately does not, and how "
        "to work on a bus without surprising it.",
        f"""
<h1>Safety</h1>
<p class="lede">CanLab can put frames on a CAN bus. This page is about what
   stops it doing that by accident, and, just as importantly, where that
   protection ends.</p>

<div class="danger">
  <span class="callout-title">Bench use only</span>
  <p>Use the transmit features only on isolated setups: a benchtop ECU,
     <code>vcan0</code>, or dedicated lab hardware. Injecting or forwarding
     frames on a live vehicle bus can interfere with braking, steering and
     airbag systems. A vehicle on jack stands with the engine running is still
     a live vehicle.</p>
</div>

{h2("The ARM TX gate")}
<p>There is one global toggle in the toolbar, <strong>ARM TX</strong>, and it
   is off when the application starts. While it is off, the paths below refuse
   to transmit. They also re-check on every frame, so turning it off stops a
   run that is already going rather than merely preventing the next one.</p>
{table(["Path", "Where"], [
    ["Signal injection, single and looping", "INJECTION &rarr; INJECT"],
    ["Replay of a capture", "INJECTION &rarr; REPLAY"],
    ["Trigger-driven sends", "INJECTION &rarr; TRIGGERS"],
    ["Actuator sweep", "INJECTION &rarr; SAFETY SCAN"],
    ["Fuzzer", "INJECTION &rarr; FUZZ"],
    ["Scripted test sequences", "INJECTION &rarr; TEST SEQUENCE"],
    ["Gateway forwarding", "GATEWAY"],
    ["UDS Clear DTC", "DIAGNOSTICS"],
    ["The REST <code>/inject</code> endpoint",
     "Needs the API token <em>and</em> ARM TX"],
])}

<div class="warn">
  <span class="callout-title">What the gate does not cover</span>
  <p>Ordinary diagnostic reads put request frames on the bus without checking
     ARM TX: the UDS scans and identifier reads, OBD-II polling, and ISO-TP
     requests. This is deliberate, on the grounds that they only read ECU
     state, but it means <strong>disarmed is not the same as silent</strong>.
     If you need the tool to emit nothing at all, disconnect the bus.</p>
</div>

{h2("The other guards")}
<ul>
  <li><strong>A warning you have to accept on first launch.</strong> The
      acceptance is remembered, so it appears once.</li>
  <li><strong>The UDS service scan is read-only by default.</strong> It probes
      only services that read. Destructive ones, such as ECU reset and clear
      diagnostics, are skipped unless you tick <em>Include destructive
      services</em> and confirm. Those do exactly what their names say.</li>
  <li><strong>Security-access brute force is rate limited</strong> and stops
      the moment the ECU reports its attempt limit. Tripping that counter can
      lock a module until it is power cycled, and on some modules
      permanently.</li>
  <li><strong>The actuator sweep has a watchdog.</strong> Give it an
      arbitration ID that should keep appearing; if that ID goes silent, the
      sweep aborts on the assumption that something upstream has cut out.</li>
  <li><strong>Plugins do not run until you enable them.</strong> See
      <a href="integrations.html#plugins">plugins</a>.</li>
</ul>

{h2("Working safely")}
<ol class="steps">
  <li><h3>Start on a virtual bus</h3><p>Everything except real ECU responses
      works on <code>vcan0</code>. Get your injection payloads right there
      first. See <a href="install.html#hardware-interfaces">hardware
      interfaces</a> for the three commands that set one up.</p></li>
  <li><h3>Then a bench ECU on its own</h3><p>One module, its own power, its own
      bus segment, terminated properly. Nothing else on the wire that you would
      mind confusing.</p></li>
  <li><h3>Watch before you write</h3><p>Capture and analyse first. If you do
      not know what a message normally carries, you cannot tell whether your
      injected version is plausible.</p></li>
  <li><h3>Arm deliberately, disarm immediately</h3><p>Turn ARM TX on for the
      moment you need it and off again after. It is a toggle rather than a
      per-action prompt precisely so that leaving it armed is a visible
      state.</p></li>
</ol>

{h2("If you are testing a real vehicle")}
<p>That is beyond what this project is built or tested for, and nothing here
   should be read as a recommendation to do it. If you do, the parts that
   matter are the ones this tool cannot give you: a way to cut power quickly,
   a vehicle that cannot move, and somebody else present. Reading is a much
   smaller risk than writing, and most reverse engineering is reading.</p>
"""))

    return pages


def build_pages_2(*, h2, table, repo):
    """Guide and tab reference."""
    pages = []

    pages.append((
        "guide.html",
        "Guide",
        "The reverse-engineering loop end to end: load a capture, narrow it "
        "down, define a signal, verify it, export.",
        f"""
<h1>Using CanLab</h1>
<p class="lede">This is the loop the application is built around. It works
   start to finish on the bundled sample capture, so you can follow it without
   any hardware.</p>

{h2("The short version")}
<p>Load a capture. Let the offline analysis tell you which bytes are counters
   and checksums so you can ignore them. Look at what is left, guess a field,
   write it down as a signal, and check the decoded value against reality. When
   it holds up, export.</p>

{h2("1. Load a capture")}
<p><strong>File &rarr; Open Log</strong>, or the toolbar button. CanLab reads
   SavvyCAN CSV, candump logs, pcap, Vector BLF and ASC, MDF4 and openpilot
   rlog; the full list with caveats is in
   <a href="reference.html#log-formats">the reference</a>. To follow along
   without a capture of your own, open
   <code>canlab/sample_data/sample_kona_drive.csv</code>.</p>
<p>The FRAMES tab fills with every frame in time order. A byte lights up when
   it changes from the previous frame with the same ID, which is the single
   most useful thing on the screen early on: the parts of a message that move
   are the parts worth investigating.</p>

{h2("2. See which IDs exist")}
<p>The panel on the left lists every arbitration ID in the capture with its
   rate and frame count. This tells you the shape of the bus: a 100 Hz message
   is almost certainly a control or sensor message, a 1 Hz one is more likely
   status or diagnostics.</p>
<p>Select an ID and the inspector on the right shows the last frames in hex, a
   per-byte activity bar, and minimum, maximum and mean for each byte. A byte
   that never changes is padding or a constant. A byte that changes every
   single frame is a counter, a checksum, or a fast-moving value.</p>

{h2("3. Remove what is not a signal")}
<p>Two of the eight bytes in a typical message are often not data at all. Go to
   <strong>AUTO-RE &rarr; COUNTER/CHECKSUM</strong> and run detection. It
   sweeps every message and reports rolling counters and checksum bytes.</p>
<p>Then <strong>ENTROPY BOUNDARIES</strong> measures per-bit entropy across a
   message to suggest where one field ends and the next begins: bits that never
   change are padding, bits that change together tend to belong to the same
   value.</p>
<p><a href="analysis.html">How the analysis works</a> explains what these
   methods actually test, and how much to trust the numbers. The short answer
   is that they narrow the search; they do not finish it.</p>

{h2("4. Guess a field and write it down")}
<p>Go to <strong>DBC BUILDER</strong> and create a signal. You need a message
   ID, a start bit, a length, a byte order and a scale. The bit grid underneath
   shows the live payload with your selected field highlighted, and converts
   between grid position and DBC bit numbering, which is the part that is easy
   to get wrong by hand.</p>
<p>Byte order is the usual trap. If a value looks like it jumps wildly when it
   should move smoothly, try the other endianness before you conclude the field
   is wrong.</p>

{h2("5. Check it against real frames")}
<p>The live decode preview under the editor runs real frames from your capture
   through the definition you just wrote and shows the physical value. This is
   the moment of truth: a wheel speed that reads 60 to 80 km/h is plausible,
   one that reads 4,000 is not.</p>
<p>Then plot it. <strong>PLOT</strong> draws the decoded value over time next
   to the raw bytes it came from. A physical quantity moves smoothly. A wrong
   byte order shows up as a sawtooth, because the high and low halves are
   swapped and the value wraps every time the low byte rolls over.</p>
<div class="note">
  <span class="callout-title">Everything decodes through one path</span>
  <p>The preview, the plot and the exported file all go through the same
     cantools-backed code in <code>core/dbc_manager.py</code>. What you see in
     the preview is what the exported DBC will produce.</p>
</div>

{h2("6. Calibrate against something real")}
<p>If you have an independent measurement of the same quantity, a GPS speed log
   for instance, you do not have to guess the scale.
   <strong>Tools &rarr; Calibrate signals from a reference file (CSV, GPX)</strong>
   takes a CSV with a time column and any value columns, or a GPX track, finds
   the clock offset between the reference and the capture, and searches for
   the CAN field whose values best fit each series by least squares, reporting
   scale, offset and an R² verdict of PASS or UNCONFIRMED. The rows you pick
   become DBC signals. See
   <a href="analysis.html#reference-calibration">reference calibration</a>.</p>

{h2("7. Export")}
<p>When the definitions hold up, get them out. <strong>File &rarr; Export
   DBC</strong>, or one of the other formats from the DBC BUILDER: openpilot
   DBC, Vector CANdb++, AUTOSAR ARXML, or a Wireshark Lua dissector so your
   signals appear by name in a packet capture. <strong>CODE GEN</strong> will
   write Python or C that opens the bus and decodes them. The formats and their
   caveats are on <a href="exports.html">the exports page</a>.</p>

{h2("Working with a live bus")}
<p>Everything above works on a file. Connecting to hardware adds two things:
   frames arrive continuously, and you can transmit.</p>
<p>Pick the adapter in <strong>Settings &rarr; CAN ADAPTERS</strong> and use
   the toolbar's <strong>Connect CAN</strong>. Frames stream into the same
   FRAMES tab and every analysis works on what has been captured so far. The
   <strong>Freeze</strong> button stops the table scrolling while you read
   something, without stopping capture.</p>
<p>Transmitting requires arming first, deliberately. Read
   <a href="safety.html">Safety</a> before you do; it explains what the gate
   covers and what it does not.</p>

{h2("A worked example on the sample")}
<ol class="steps">
  <li><h3>Open the sample</h3><p><code>canlab/sample_data/sample_kona_drive.csv</code>,
      6,610 frames, 10 IDs.</p></li>
  <li><h3>Run counter and checksum detection</h3><p>AUTO-RE finds the rolling
      counters and the trailing checksum bytes, so you know which bytes to
      ignore.</p></li>
  <li><h3>Look at ID 0A6</h3><p>It runs at 50 Hz and its bytes move
      smoothly, which is what a wheel-speed message looks like.</p></li>
  <li><h3>Define a 16-bit big-endian signal</h3><p>Start bit 7, length 16,
      scale 0.03125, on message 0A6.</p></li>
  <li><h3>Read the preview</h3><p>Values in the 60 to 80 range with a unit of
      km/h. That is the right order of magnitude for a wheel speed, so the
      scale is plausible.</p></li>
  <li><h3>Plot it</h3><p>A smooth rise and fall, not a sawtooth. The byte order
      is right.</p></li>
  <li><h3>Export the DBC</h3><p>And load it in any tool that reads DBC to
      confirm it round-trips.</p></li>
</ol>
"""))

    return pages


def build_pages_3(*, h2, table, repo):
    """Tab reference."""
    pages = []
    pages.append((
        "tabs.html",
        "Tabs",
        "What each of the 16 tabs does and when you would reach for it.",
        f"""
<h1>The 16 tabs</h1>
<p class="lede">Roughly in the order you would use them.</p>

{h2("Finding your way around")}
<p>Sixteen tabs holding 33 sub-tabs is 49 panes, too many for one row. The
   layout follows Blender: layered greys so nesting reads as depth, blue for
   selection, and green, amber and red kept only where they mean connected,
   pending and armed.</p>
{table(["", ""], [
    ["<strong>Workspaces</strong>",
     "The tabs are grouped into CAPTURE, EXPLORE, DETECT, DEFINE and BUS. The "
     "bar follows the tabs as well as driving them, so Alt+1..9 and Ctrl+Tab "
     "still work and the bar switches workspace to keep up."],
    ["<strong>Command palette</strong>",
     "Ctrl+Shift+P or F3 searches 105 commands: every pane by its path and "
     "every menu action with its shortcut. Both lists are read from the live "
     "window, so nothing is registered by hand."],
    ["<strong>Sidebars</strong>",
     "The ID list and the inspector sit in a real splitter. Drag them, "
     "collapse them to nothing with T and N, or both at once with "
     "Ctrl+Space. Widths and state are remembered."],
    ["<strong>Reduce motion</strong>",
     "View &gt; Reduce Motion turns animation off. It also stands down while a "
     "live capture runs, except the armed and connected indicators."],
])}
<p>The minimum window size is 1124 by 851, so it fits a laptop screen with both
   sidebars open.</p>

{h2("FRAMES")}
<p>The raw view: every frame in time order with its timestamp, arbitration ID,
   bus, length, data bytes and the gap since the last frame with the same ID. A
   byte is highlighted when it differs from the previous frame with that ID,
   which is how you spot movement at a glance.</p>
<p>Filter by ID (hex substring) or bus. <strong>Freeze</strong> stops the table
   updating while you read, without stopping capture. <strong>Follow</strong>
   keeps it scrolled to the newest frame. Double-click a row for the full frame
   detail.</p>

{h2("SNIFFER")}
<p>One row per message instead of one per frame, which is the shape of the
   question you actually ask at the bench: I pressed the button, what changed.
   Each byte is coloured by what it just did, <strong>green</strong> when it
   rose and <strong>red</strong> when it fell, fading back after a second.
   Bytes that have never moved sit dim, so the active ones stand out.</p>
<p><strong>Notch</strong> is the reason to use this rather than FRAMES. It
   records every bit currently in motion and ignores it from then on. Press it
   a few times while the vehicle idles and the wheel-speed counters, the
   checksums and the sensor jitter all go quiet; the bit that lights up next is
   the one you caused. Un-notch forgets the mask. There is also a bit view, an
   option to keep silent IDs on screen rather than letting them expire after
   five seconds, and one to blank notched bits entirely.</p>
<p>Modelled on SavvyCAN's sniffer window and on <code>cansniffer</code>. It
   works on a live bus and on a loaded capture; on a file there is no "now", so
   nothing expires. Rendering 180 IDs costs about 40 ms, and the table updates
   in place rather than rebuilding.</p>

{h2("SIGNALS")}
<p>One row per message rather than per frame: frame count, rate, payload
   entropy and a suspected message type. It is the fastest way to see the shape
   of a capture. Entropy is the useful column: a message whose payload never
   changes is not interesting, and one with high entropy across every byte is
   often a diagnostic or multiplexed message rather than a set of signals.</p>

{h2("PLOT")}
<p>Multi-signal time series. Raw bytes and decoded DBC signals share one time
   axis, each with its own scale, so you can compare a decoded value against
   the bytes it came from or against a different message entirely. Mouse wheel
   zooms.</p>
<p>This is where you confirm a definition. Physical quantities move smoothly; a
   sawtooth usually means the byte order is wrong.</p>

{h2("AI ENGINE")}
<p>Optional, and off until you configure a provider. Sends one message ID's
   statistics to Anthropic, OpenAI, Groq or a local Ollama model and asks for an
   interpretation. What makes it more useful than pasting hex into a chat
   window is that the offline findings go with the question: the byte roles,
   the detected checksum, the message period. Memory persists across sessions.
   See <a href="integrations.html#ai-engine">what actually leaves the
   machine</a>.</p>

{h2("DBC BUILDER")}
<p>Where a guess becomes a definition. A form for the signal fields, a bit grid
   showing the live payload with your selection highlighted, and a live decode
   preview running real frames from the capture through the definition.</p>
<p>Imports DBC, ARXML and Excel or CSV CAN matrices. Exports DBC, openpilot
   DBC, Vector CANdb++, ARXML and a Wireshark Lua dissector. There is also an
   <em>Auto-Build DBC</em> button that drafts definitions from the detectors,
   and a cross-reference against opendbc.</p>

{h2("CODE GEN")}
<p>Turns your definitions into a working program. Pick the signals, the
   interface and the direction, and it writes Python or C that opens the bus
   and decodes them, or encodes and sends them.</p>

{h2("INTELLIGENCE")}
<p>Looks across messages rather than within one. Its sections are signal
   periodicity, automatic DBC generation, log diff, an opendbc cross-reference,
   change-on-action capture, a J1939 and NMEA 2000 PGN decoder, and a value
   reverse lookup.</p>
<p>The PGN decoder works the protocol out from the identifier: NMEA 2000 uses
   data page 1 in the 126208 to 130836 range. Multi-frame PGNs are reassembled
   first, J1939 transport protocol and NMEA 2000 fast packets alike, and
   decoded whole; one frame of one is never decoded alone, because that gives
   a confident wrong answer.</p>
<p>Change-on-action is the one worth knowing about: capture a baseline, perform
   a physical action, capture again, and it shows which bytes changed. That is
   often the fastest route from "somewhere in these 60 messages" to a
   candidate.</p>

{h2("INJECTION")}
<p>Everything that transmits, in six sub-tabs: <strong>INJECT</strong> a signal
   at a physical value once or in a loop, <strong>REPLAY</strong> a capture
   back onto the bus with a scrubber, <strong>TRIGGERS</strong> that fire on a
   byte condition, <strong>SAFETY SCAN</strong> which sweeps an actuator
   between limits with a watchdog, <strong>FUZZ</strong> with random, boundary
   or mutation payloads, and <strong>TEST SEQUENCE</strong> for scripted
   inject, wait and assert steps.</p>
<p>The INJECT page previews the frame it would send before anything is armed,
   colouring each byte by whether the signal or the vehicle profile wrote it,
   and keeps a log of every send with its result.</p>
<div class="warn">
  <span class="callout-title">Gated</span>
  <p>Nothing here transmits until ARM TX is on, and turning it off stops a run
     already in progress. Read <a href="safety.html">Safety</a>.</p>
</div>

{h2("DIAGNOSTICS")}
<p>The request side, in six sub-tabs: OBD-II and UDS, a UDS deep scan, a UDS
   service scan, security access, bus load and bus health. Full protocol detail
   is on <a href="diagnostics.html">the diagnostics page</a>.</p>

{h2("DASHBOARD")}
<p>The whole bus at once. A byte-value heatmap across every message, so a dense
   column is a message worth investigating and a blank one is padding; a
   message timeline showing when each ID is active; and physical overlay gauges
   driven by signals you have defined.</p>

{h2("AUTO-RE")}
<p>The tedious part, automated. Six sub-tabs: <strong>COUNTER/CHECKSUM</strong>
   detection across every message, <strong>ENTROPY BOUNDARIES</strong> to
   suggest where fields begin and end, <strong>CORRELATION</strong> between
   bytes, a <strong>CHECKSUM GUESSER</strong> that takes one message and
   one byte and tries every algorithm it knows, <strong>FLAGS &amp; ENUMS</strong>
   for switches and value tables, and <strong>BLOCKS</strong> for runs of
   consecutive IDs that share one layout, with a shared field added to every
   member at once. Runs in worker threads, so the window stays responsive. See
   <a href="analysis.html">how it works</a>.</p>

{h2("TIMELINE")}
<p>Questions about <em>when</em>. Several signals stacked on one scrubbable
   axis with a shared playhead, so you can line up the moment a flag flips
   against the moment a value starts moving. The <strong>VIDEO SYNC</strong>
   sub-tab loads a dashcam or bench recording and ties it to the log with an
   adjustable offset: click a signal spike to seek the video, scrub the video
   to move the playhead.</p>

{h2("OBD-II")}
<p>The standardised subset any compliant vehicle answers. Discovers which PIDs
   are supported by walking the continuation windows rather than assuming the
   first 32, then polls the ones you pick and shows them as live gauges.</p>

{h2("ML INTEL")}
<p>Per-byte rather than per-message. Classifies each byte as a counter,
   checksum, boolean flag, physical value or padding with a confidence, fits a
   baseline from normal traffic and scores frames against it for anomalies, and
   finds messages with similar behaviour by embedding.</p>
<p>The <strong>WATCH</strong> sub-tab does the scoring live: fit a baseline
   from the last half minute, start the watch, and each new batch of frames is
   scored as it arrives. A payload out of band, an ID gone quiet, a burst or an
   ID the baseline never saw becomes a row, a flash in the status bar and, if
   you ask, a mark on the timeline.</p>

{h2("GATEWAY")}
<p>Bridges two CAN channels and puts you in between. Rules are applied in
   order: pass a message through, block it, or modify a byte or the arbitration
   ID as it crosses. That is how you test what an ECU does when a message it
   depends on disappears or arrives with a different value.</p>
<p>It needs <strong>two</strong> hardware channels, and like everything else it
   forwards nothing until ARM TX is on.</p>
"""))
    return pages


def build_pages_4(*, h2, table, repo):
    """Analysis, diagnostics, exports."""
    pages = []

    pages.append((
        "analysis.html",
        "Analysis",
        "How the offline detection actually works, and how much to trust it.",
        f"""
<h1>How the analysis works</h1>
<p class="lede">All of this runs locally with no API key and nothing leaving
   the machine. It is also all heuristic, so this page is as much about the
   limits as the methods.</p>

<div class="warn">
  <span class="callout-title">Read this first</span>
  <p>These methods produce <strong>candidates</strong>. A confidence figure is
     a match fraction over the frames you loaded, not a statistical proof, and
     not a probability that the answer is right. A byte can satisfy a checksum
     relation by coincidence, especially in a short capture. Verify against the
     vehicle before you rely on anything here.</p>
</div>

{h2("Counters and checksums")}
<p><code>core/counter_checksum_detector.py</code> sweeps every message.</p>
<p>A <strong>counter</strong> is a byte, or a nibble of one, that increments by
   one from frame to frame and wraps. The detector checks the whole byte and
   each nibble separately, because four-bit counters packed into the top or
   bottom half of a byte are common.</p>
<p>A <strong>checksum</strong> is a byte that can be computed from the others.
   The detector tests whether each byte is the sum, the exclusive-or, or the
   nibble sum of the rest.</p>
<p>Knowing which bytes these are matters more than it sounds: they change
   constantly, so they look exactly like fast-moving signals until you rule
   them out.</p>

{h2("Identifying the checksum algorithm")}
<p>The sweep above tells you <em>which byte</em>. To find out <em>which
   algorithm</em>, <strong>AUTO-RE &rarr; CHECKSUM GUESSER</strong> takes one
   message and one byte and tries them all: plain and inverted sums, two's
   complement, nibble sums, CRC-8 in its SAE J1850 and AUTOSAR forms, and the
   manufacturer variants used by Hyundai, Toyota, Honda and Subaru
   (<code>core/checksums.py</code>).</p>
<p>It fits on the first 70% of the capture and validates on the remaining 30%,
   then reports both numbers. That split is the useful part: an algorithm that
   scores well on the training portion and badly on the validation portion has
   memorised noise rather than found the rule.</p>

{h2("Entropy boundaries")}
<p><code>core/entropy_boundary.py</code> measures per-bit entropy across a
   message. Bits that never change are padding. Bits that change constantly
   carry something. Runs of bits with similar entropy tend to belong to the
   same field, so the boundaries between runs suggest where one signal ends and
   the next begins.</p>
<p>It is a suggestion about <em>structure</em>, not meaning. It will happily
   draw a boundary through the middle of a 16-bit value whose high byte rarely
   changes.</p>

{h2("Cross-ID correlation")}
<p><code>core/correlation_engine.py</code> computes Pearson r between byte
   pairs across different messages, aligning them to nearest timestamps and
   sweeping a range of lags.</p>
<p>This is how you find the same physical quantity reported by two ECUs, which
   is useful because one of them is often easier to identify than the other. A
   high correlation between a byte you understand and one you do not is a
   strong lead.</p>
<p>Bear in mind that with thousands of aligned samples almost anything reaches
   statistical significance, so judge by the size of r, not by whether a test
   passed.</p>

{h2("Byte role classification")}
<p><code>core/signal_classifier.py</code> labels each byte COUNTER, CHECKSUM,
   BOOLEAN, PHYSICAL or PADDING, and ML INTEL shows the same with a confidence
   and the entropy behind it. It combines the detectors above with simple
   distribution tests: a byte with two values is a flag, a byte with a smooth
   distribution is more likely a physical quantity.</p>

{h2("Anomaly detection")}
<p><code>core/anomaly_detector.py</code> fits a baseline from traffic you
   declare normal, then scores later frames against it, using a z-score per
   byte and an Isolation Forest over the frame vector. The use is finding the
   one message that behaves differently when a fault is present or a button is
   pressed.</p>
<p><code>core/live_watch.py</code> runs the same baseline against a bus as it
   is captured. Each batch of new frames is scored in one vectorised call; a
   payload far from the baseline is reported once per ID per cooldown, an ID
   that stops arriving once until it returns, a burst when an ID arrives far
   faster than its fitted period, and an unknown ID once. Time is the frame
   clock, so a replayed log gives the same events every time. Replaying the
   23-minute car log from the real-data corpus after a 60 s fit costs 1 ms per
   2,000-frame batch. A short fit window flags legitimate range changes, which
   is what a per-byte baseline does; fit on a stretch that covers what you
   expect to see.</p>

{h2("Multiplexer detection")}
<p><code>core/mux_detector.py</code> looks for a selector byte whose value
   changes which other bytes are active. Multiplexed messages otherwise look
   like nonsense, because the same byte offset means different things depending
   on the mode.</p>

{h2("Repeated blocks")}
<p>A battery pack with 96 cells does not get 96 signals in one frame. It gets
   a run of consecutive identifiers, each carrying the same layout for a few
   cells, all at the same rate and length. Per-ID analysis sees unrelated
   messages. <code>core/block_detector.py</code> sees the run: consecutive IDs
   (a gap of one identifier allowed) with the same DLC and a rate within
   tolerance, a score for how far the members agree on which bytes are
   constant, counters, values or noise, and a proposal for the field they
   share. Candidates are 8-bit bytes and 16-bit words at even offsets; the
   byte order is the one that reads more smoothly, and a word whose low byte
   never moves, or whose high byte is always zero, is dropped because the
   byte proposal already covers it.</p>
<p>One decision then becomes a candidate signal in every member. Every name
   ends in <code>CANDIDATE</code> and every description says the scale is
   unknown: a shared structure is evidence of a repeated layout, not of what
   it means. On a private EV capture of 460,024 frames the 27-message block
   0x380 to 0x39A at 2 Hz is found in 0.23 s, with nine members that never
   change and a layout that turns out not to be uniform, which the
   consistency figure says plainly.</p>

{h2("Reference calibration")}
<p><code>core/reference_calibrate.py</code> is the one method here that can
   give you a definitive answer, because it uses ground truth.</p>
<p>Give it an independent measurement and it searches across arbitration IDs,
   byte ranges and endianness for the field whose values best fit by least
   squares. It reports scale, offset and an R² verdict of PASS or
   UNCONFIRMED. <code>core/reference_series.py</code> reads the measurement
   from a CSV with a time column and any number of value columns, keeping a
   unit written in the header as <code>speed (km/h)</code>, or from a GPX
   track, from which it derives speed by haversine distance over time,
   altitude, latitude and longitude. Each series is calibrated on its own.</p>
<p>Two clocks rarely agree: a GPS logger stamps epoch seconds while a capture
   may start at zero, and even on one clock a phone and an adapter drift a
   few seconds apart. The search first finds the lag that lines the reference
   up with some field in the capture, a coarse pass over binned values across
   the window and then a fine pass sample by sample, with ties toward the
   smaller lag. Overlapping wins on one ID, a 16-bit word and the byte inside
   it, collapse to the best reading. On the shipped sample a reference put one
   second ahead is found at 1.0 s and the wheel speed comes back as four
   16-bit big-endian words at scale 1/32 with R² 0.9987. A periodic reference
   is ambiguous at lags near a multiple of its period; keep the window under
   half of it.</p>
<p>Two refinements matter in practice. Sentinel codes that mean "signal
   unavailable", typically all bits set, are masked so they do not wreck the
   fit. And a fitted scale is snapped to a neat value when doing so barely
   changes the decode, because a real scale is far more likely to be 0.03125
   than 0.031248. Both are adapted from CSS Electronics' reverse-engineering
   skills.</p>
<p>The dialog (Tools &rarr; Calibrate signals from a reference file) runs the
   sweep off the GUI thread with a progress bar and a Cancel button, and the
   rows you pick become DBC signals as one undoable step, with the Motorola
   start bit written correctly for big-endian fields.</p>

{h2("Performance")}
<p>Counter and checksum detection is vectorised over NumPy arrays rather than
   iterating rows: on a 500,000-frame capture it went from about 183 seconds to
   about 2.8 seconds. The heavier analyses run in worker threads so the window
   stays responsive.</p>
"""))

    pages.append((
        "diagnostics.html",
        "Diagnostics",
        "UDS, ISO-TP, OBD-II and J1939, plus the protocols that ship as "
        "library modules only.",
        f"""
<h1>Diagnostics</h1>
<p class="lede">Talking to ECUs rather than listening to the bus. Everything
   here sends request frames, so read <a href="safety.html">Safety</a> first,
   and note that ordinary reads are <em>not</em> behind the ARM TX gate.</p>

{h2("What the tab holds")}
<p>Six sub-tabs: OBD-II and UDS, UDS deep scan, UDS services, security access,
   bus load and bus health.</p>

{h2("ISO-TP")}
<p><code>core/isotp.py</code> implements ISO 15765-2, the transport that
   carries diagnostic messages larger than eight bytes: single frames,
   multi-frame transmission with the flow-control handshake and separation
   time, and reassembly of responses.</p>
<p>You rarely touch this directly, but it is the layer everything else rides
   on, so when a scan returns nothing this is often where the problem is.</p>

{h2("UDS")}
<p><code>core/uds.py</code> implements ISO 14229 requests: read diagnostic
   trouble codes, read ECU identification by data identifier, and scan which
   services an ECU supports.</p>
<div class="warn">
  <span class="callout-title">The service scan is read-only by default</span>
  <p>Probing a service like ECU Reset or Clear Diagnostic Information does
     exactly what the name says. Destructive services are skipped unless you
     tick <em>Include destructive services</em> and confirm.</p>
</div>
<p>DTCs come back as four-byte records: three bytes of code plus a status byte.
   The printable form follows ISO 15031-6, so the high two bits select the
   letter (P, C, B or U) and the rest give the digits.</p>

{h2("Security access")}
<p><code>core/security_access.py</code> handles service 0x27, the seed and key
   exchange that unlocks a protected session. It requests a seed and tries
   common algorithms, or your own key function from a Python script; there is
   an example at <code>canlab/sample_data/example_seedkey.py</code>.</p>
<div class="danger">
  <span class="callout-title">Attempt limits are real</span>
  <p>Brute force is rate limited and stops the moment the ECU reports that its
     attempt limit is reached. Tripping that counter can lock a module until it
     is power cycled, and on some modules permanently. This is not a feature to
     point at a part you cannot replace.</p>
</div>

{h2("OBD-II")}
<p><code>core/obd2_pids.py</code> holds the canonical 26-PID table with
   correct one- and two-byte decoders. Supported-PID discovery walks the
   continuation windows rather than assuming the first 32, so PIDs above 0x20
   are found.</p>
<p>This is the one protocol where you can expect an answer from any compliant
   vehicle without knowing anything about it, which makes it a good first test
   that your interface and wiring work at all.</p>

{h2("J1939 and NMEA 2000")}
<p><code>core/j1939.py</code> decodes parameter group numbers for heavy
   vehicles, and decodes DM1 active diagnostic trouble codes into SPN, FMI, CM
   and OC fields.</p>
<p>Marine NMEA 2000 uses the same 29-bit frame, so the data page decides which
   table applies. Single-frame NMEA 2000 PGNs such as vessel heading, rate of
   turn, rapid position, course and speed, wind and temperature are decoded.
   Every layout is checked in the tests against frames from a real recording.</p>
<p>Messages that span several frames are reassembled by
   <code>core/multiframe.py</code> before decoding: J1939 transport protocol,
   both BAM broadcasts and RTS/CTS sessions between two other nodes (observed
   only; CanLab never sends a CTS), and NMEA 2000 fast packets with their
   sequence and counter byte. Timeouts run on the frame clock. On the marine
   recording this rebuilds 60 GNSS position fixes of 43 bytes, decoded to a
   position, date, time, altitude and satellite count, and 60 satellite lists
   of 135 bytes; on the truck log, 85 BAM broadcasts of engine and retarder
   configuration with nothing dropped. The PGN scan in INTELLIGENCE shows the
   reassembled messages; <code>list_pgns</code> and
   <code>list_transport_messages</code> serve them over MCP. RTS/CTS is tested
   against synthetic frames, because no recording in the corpus has one.</p>

{h2("Bus load and health")}
<p>Two monitor sub-tabs. Load shows utilisation over time. Health tracks error
   frames, bus-off events and arbitration IDs that go silent, which is the
   quickest way to notice that something you did upset a module.</p>

{h2("XCP and DoIP")}
<p>Both have panels in DIAGNOSTICS.</p>
{table(["Module", "What it does"], [
    ["<code>core/xcp.py</code>",
     "XCP over CAN, read-only: CONNECT, UPLOAD and SHORT_UPLOAD plus a "
     "measurement poller. It deliberately implements no memory-write or "
     "programming commands, so it cannot put a value into an ECU."],
    ["<code>core/doip.py</code>",
     "DoIP (ISO 13400) over stdlib sockets: vehicle discovery, routing "
     "activation and UDS over IP."],
])}
"""))

    pages.append((
        "exports.html",
        "Exports",
        "Getting your definitions out: DBC, openpilot, CANdb++, ARXML, "
        "Wireshark and generated code.",
        f"""
<h1>Exports and code generation</h1>
<p class="lede">A signal definition is only useful if the next tool can read
   it. Everything here goes through one cantools-backed path, so what the
   preview decodes is what the file produces.</p>

{h2("The formats")}
{table(["Format", "Import", "Export", "Notes"], [
    ["Standard DBC", "Yes", "Yes",
     "The lingua franca. Parseable by cantools and the Vector tools."],
    ["openpilot DBC", "Yes, as a cross-reference", "Yes",
     "Carries the comma.ai checksum and counter annotations."],
    ["Vector CANdb++", "No", "Yes",
     "With <code>BA_DEF_</code> attribute blocks."],
    ["AUTOSAR ARXML 4.3", "Yes", "Experimental",
     "Round-trips within CanLab but is <strong>not</strong> validated against "
     "the full AUTOSAR schema. Do not rely on it in external AUTOSAR tools "
     "yet."],
    ["Wireshark Lua dissector", "No", "Yes",
     "Your signals appear by name in a packet capture. Little- and "
     "big-endian; the big-endian path is verified against cantools."],
    ["Excel or CSV CAN matrix", "Yes", "No",
     "For the spreadsheets that OEM documentation often arrives as."],
    ["Decoded time series", "No", "Yes",
     "A timestamp-by-signal matrix to CSV or Parquet, for analysis "
     "elsewhere."],
])}
<p>Extended 29-bit identifiers, multiplexed signals and value tables
   round-trip.</p>

{h2("Cross-referencing opendbc")}
<p><strong>Tools &rarr; Match against opendbc</strong> fetches the
   <a href="https://github.com/commaai/opendbc">commaai/opendbc</a> library,
   caches it under <code>~/.canlab/opendbc_cache</code>, and ranks how well
   your capture's arbitration IDs match each OEM database. The first run needs
   network access; afterwards it works from the cache.</p>
<p>If your vehicle is one openpilot already supports, this can shortcut most of
   the work. If it is not, a partial match still tells you which OEM's
   conventions to expect.</p>

{h2("Generated code")}
<p><strong>CODE GEN</strong> writes a working program from your definitions:
   Python that opens the bus and decodes the signals you picked, or encodes and
   sends them, and a C variant that does the bit extraction directly. Pick the
   signals, the interface and the direction.</p>
<p>The generated Python goes through cantools for encoding, so a frame it
   produces is formed the same way the preview decodes it.</p>

{h2("Projects")}
<p><strong>File &rarr; Save Project</strong> writes a <code>.canlab</code> file
   holding the frames, the signal definitions and your notes together, so a
   session can be picked up later or handed to somebody else. It is the only
   format here that round-trips the <em>work</em> rather than the result.</p>
"""))
    return pages


def build_pages_5(*, h2, table, repo):
    """Integrations and reference."""
    pages = []

    pages.append((
        "integrations.html",
        "Integrations",
        "REST API, MCP server, plugin SDK, AI providers and hardware "
        "backends.",
        f"""
<h1>Integrations</h1>
<p class="lede">Ways to drive CanLab from something else, and ways to extend
   it.</p>

{h2("REST API")}
<p>Start it from the <strong>REST API</strong> toolbar toggle. It binds to
   <code>127.0.0.1:8765</code> and prints a per-session token on start. Every
   request needs an <code>X-API-Token</code> header.</p>
<pre><code>GET  /            # live web dashboard (HTML, open)
GET  /frames      # last N frames  (?n=N)
GET  /signals     # decoded DBC signals
GET  /status      # connection state and frame count
GET  /memory      # AI memory entries
POST /mark        # add an event mark: {{"label":"brake","action":"toggle"}}
                  # action is toggle (default), begin, end or point
POST /inject      # inject a frame: needs the token AND ARM TX
                  # {{"id":"0x200","data":"01 02 03 04 05 06 07 08"}}</code></pre>
<p>Loopback only. It is meant for scripting the tool from the same machine, not
   for exposing a bus to a network. <code>/mark</code> is what a phone uses to
   say "this is the brake" while someone else drives; the mark lands on the
   INTELLIGENCE tab's list and the TIMELINE.</p>
<div class="note">
  <span class="callout-title">Injection is doubly gated</span>
  <p><code>/inject</code> needs both a valid token and ARM TX on. The token
     alone will not transmit.</p>
</div>

{h2("MCP server: Claude, ChatGPT and Codex")}
<p>CanLab is a Model Context Protocol server. An assistant connected to it can
   load a capture, list IDs, read byte statistics and raw frames, run every
   detector, draft a DBC, define and remove signals, decode frames, annotate
   the timeline and rank bytes against those annotations, list reassembled
   multi-frame messages, find repeated blocks, calibrate against a reference
   file and read the live watch's events: 29 tools in
   <code>canlab/core/mcp_tools.py</code>. <strong>No MCP tool transmits.</strong>
   Putting frames on a wire stays behind ARM TX in the window, where a person
   is watching.</p>
<p>The same tools are served two ways.</p>
{table(["Where", "How"], [
    ["<strong>Inside the window</strong>",
     "The <strong>MCP</strong> toolbar toggle, or Settings &rarr; MCP, starts a "
     "Streamable HTTP server on <code>127.0.0.1:8766/mcp</code> over the capture "
     "you have loaded or are recording right now. A signal the assistant adds "
     "appears in DBC BUILDER as one undoable step."],
    ["<strong>Headless</strong>",
     "<code>canlab-mcp</code> serves stdio and loads captures on request; "
     "<code>canlab-mcp --http</code> serves the same over HTTP with no window."],
])}
<p>Settings &rarr; MCP writes the exact configuration for each client and copies
   it to the clipboard. In short:</p>
<pre><code># Claude Code, against the running window (or a headless --http server)
claude mcp add --transport http canlab http://127.0.0.1:8766/mcp

# Claude Desktop and Codex CLI launch stdio servers, so bridge to the window
canlab-mcp --attach http://127.0.0.1:8766/mcp

# Claude Desktop, headless, no window needed
{{"command": "/path/to/.venv/bin/canlab-mcp"}}</code></pre>
<p>ChatGPT connects from OpenAI's servers, so it cannot reach your loopback
   address. Publish the server over HTTPS first, with
   <code>cloudflared tunnel --url http://127.0.0.1:8766</code> or ngrok, tick
   <em>allow connections from other machines</em>, and add
   <code>https://&lt;tunnel-host&gt;/mcp</code> as a connector. The
   <code>search</code> and <code>fetch</code> tools exist for ChatGPT's
   connector contract; Developer mode exposes the rest.</p>
<div class="warn">
  <span class="callout-title">A tunnel has no authentication</span>
  <p>The in-window server takes an optional bearer token, but ChatGPT's
     connectors cannot send one. While a tunnel is up, anyone who learns the
     URL can read the capture and edit the signal list. Nothing can transmit,
     but close the tunnel when you are done.</p>
</div>

{h2("Capture kit")}
<p>A desktop captures well when someone is sitting at it. A day of driving
   needs a logger that starts at boot, writes to disk as it goes, and lets the
   driver say "this is the brake" without a screen. <code>canlab-cli
   capture</code> is that logger; see <a href="cli.html#capture">the command
   line page</a>. It serves the same REST API as the window, with
   <code>/mark</code> and <code>/frames</code> but without <code>/inject</code>,
   so a phone on the same network can add marks while the kit records.</p>

{h2("Plugins")}
<p>Drop a <code>.py</code> file into <code>~/.canlab/plugins/</code>. It needs
   three things:</p>
<pre><code>PLUGIN_NAME    = "My Plugin"
PLUGIN_VERSION = "1.0"

def register(app):
    # called with the MainWindow instance when the plugin is activated
    ...</code></pre>
<p>From <code>app</code> you reach the shared application state, the loaded
   frames as a pandas DataFrame, the signal definitions, the menu bar, and
   anything in <code>canlab.core</code>. Full API, the event list and two
   worked examples are in
   <a href="PLUGINS.md">docs/PLUGINS.md</a>.</p>
<div class="warn">
  <span class="callout-title">Plugins run with full application privileges</span>
  <p>Metadata is read statically, so listing plugins never executes anything.
     Code runs only after you approve it, and approval is trust-on-first-use
     keyed by the file's SHA-256: editing an approved plugin changes its hash
     and asks again, so approved code cannot be silently swapped. Only approve
     plugins you trust.</p>
</div>

{h2("AI engine")}
<p>Four providers: <strong>Anthropic</strong>, <strong>OpenAI</strong>,
   <strong>Groq</strong> and <strong>Ollama</strong>, the last running locally
   with no key and nothing leaving the machine. Configure in
   <strong>Settings &rarr; API KEYS</strong>; the model field is editable, so a
   model id newer than the built-in list can be typed in.</p>
<p>What is sent, when you click Analyze on an ID, is that message's statistics:
   byte roles, message type and period, the checksum guess, similar IDs, and a
   sample of frames. Not your whole capture, and nothing at all until you
   supply a key and ask.</p>
<p>The offline findings are included in the prompt deliberately, so the model
   reasons about measured facts rather than raw hex. Its answers are still
   suggestions; treat them the way you would treat the heuristics.</p>

{h2("Hardware adapters")}
<p><strong>Settings &rarr; CAN ADAPTERS</strong> keeps a list of named adapters
   and the toolbar switches between them, so a bench with a PEAK on one port
   and a CANable on another is two clicks rather than two retypings. Each
   adapter is a backend, a channel, a bitrate, a CAN FD flag and whatever else
   that backend needs, such as an slcan stick's serial baud rate or a
   socketcand host.</p>
<p><strong>Detect connected</strong> asks every python-can backend what it can
   see, reads CAN network devices from sysfs, and recognises common USB sticks
   by vendor and product id. <strong>Test</strong> opens the adapter and listens
   for one second; it never transmits, and when opening fails it names the fix,
   whether that is the <code>ip link</code> command, a missing pip package or
   the dialout group.</p>
{table(["Backend", "Notes"], [
    ["<code>socketcan</code>",
     "Linux. The kernel owns the bitrate, so bring the device up first. "
     "PEAK, Kvaser, candleLight and 8devices adapters all appear here."],
    ["<code>gvret</code>",
     "<strong>SavvyCAN's own hardware</strong>: Macchina M2 and A0, EVTV "
     "CANDue, ESP32RET. python-can ships no backend for it, so CanLab supplies "
     "one in <code>core/gvret.py</code> and registers it as an interface. Give "
     "it a serial port, or <code>&lt;ip&gt;:23</code> for a board on WiFi."],
    ["<code>slcan</code>", "CANable with slcan firmware, USBtin, Lawicel."],
    ["<code>gs_usb</code>", "candleLight over libusb, where there is no kernel driver."],
    ["<code>pcan</code>, <code>kvaser</code>, <code>vector</code>, <code>ixxat</code>",
     "Vendor drivers, which come from the vendor."],
    ["<code>virtual</code>, <code>udp_multicast</code>",
     "No hardware at all, for trying the application."],
])}
<p><code>pip install canlab[adapters]</code> adds pyserial and gs_usb. A
   comma.ai Panda is supported separately through
   <code>core/panda_backend.py</code>, presented as a python-can compatible bus
   with the safety model selectable. The multi-bus configuration in
   <strong>Settings &rarr; MULTI-BUS</strong> captures from more than one
   interface at once, which is what you want when a vehicle has separate
   powertrain and body buses.</p>

{h2("Trimming a capture")}
<p><strong>Tools &rarr; Trim capture</strong> cuts the loaded capture down to a
   time window, a frame or percentage range, a set of IDs or ID ranges, or one
   bus, with a live count as you type. The result either replaces what is
   loaded or is written to a file. Every analysis runs over whatever is loaded,
   so trimming first makes the detectors faster and their output shorter.</p>

"""))

    pages.append((
        "reference.html",
        "Reference",
        "Log formats, settings, limitations and where things live on disk.",
        f"""
<h1>Reference</h1>
<p class="lede">The details that do not belong anywhere else.</p>

{h2("Log formats")}
{table(["Format", "Notes"], [
    ["SavvyCAN CSV",
     "GVRET and SavvyCAN exports. Handles both the hex byte format real "
     "exports use and older decimal ones, and the trailing comma SavvyCAN "
     "writes after the last data byte."],
    ["candump <code>.log</code>",
     "<code>candump -l</code> output, including CAN FD lines."],
    ["pcap / pcapng",
     "Linux SocketCAN, link type 227, via dpkt."],
    ["Vector BLF", "Through python-can's reader."],
    ["Vector ASC", "Through python-can's reader."],
    ["MDF4 <code>.mf4</code> / <code>.mdf</code>",
     "CANedge and similar. Needs <code>asammdf</code>."],
    ["openpilot <code>.rlog</code> / <code>.qlog</code>",
     "Needs pycapnp and the cereal schema. Raises a clear error if either is "
     "missing rather than guessing."],
])}
<p>Every parser produces the same columns, so the rest of the application does
   not care where a capture came from: <code>Timestamp, ID, Bus, DLC,
   Extended, B0..B7</code>, plus a per-ID <code>Delta</code>.</p>

{h2("Where things live")}
{table(["Path", "What"], [
    ["<code>~/.canlab/plugins/</code>", "Plugins you have installed."],
    ["<code>~/.canlab/opendbc_cache/</code>",
     "Cached opendbc index, so matching works offline after the first fetch."],
    ["<code>~/.canlab/memory.json</code>", "AI engine memory across sessions."],
    ["<code>canlab/sample_data/</code>",
     "The bundled sample capture, its generator, and an example seed-key "
     "script."],
    ["<code>docs/AUDIT_FIXES.md</code>",
     "The full list of defects fixed in the deep-audit pass, each with a "
     "regression test."],
])}

{h2("Settings")}
<p>Nine panels: API KEYS, CAN ADAPTERS, VEHICLE, REST API, MCP, BACKEND,
   MULTI-BUS, PLUGINS and frame cap. The ones you will actually touch are CAN
   ADAPTERS (add, detect and test your hardware), API KEYS if you want the AI
   features, MCP to connect an assistant, and PLUGINS to approve anything you
   have installed. Everything persists across restarts.</p>

{h2("Validated against real captures")}
<p>Separately from the unit suite, the whole application is run end to end over
   real vehicle recordings, because synthetic data agrees with whatever the
   code assumes. Two corpora, ninety checks.</p>
{table(["Corpus", "What it is", "Checks"], [
    ["SavvyCAN examples", "12,974 frames, 180 IDs, 11-bit, one bus", "36"],
    ["CANedge recordings and python-can format files",
     "2 to 154,896 frames, native MDF4, 11-bit and 29-bit, dual-bus, CAN FD "
     "and error frames", "54"],
])}
<p>The second corpus is other people's hardware output, none of it produced
   here: five CANedge logger recordings in native MDF4 from CSS Electronics.
   They are different kinds of bus: a 145,534-frame J1939 log that is 29-bit
   end to end, a 22.8-minute two-channel car recording of 154,896 11-bit
   frames, and a 9,600-frame marine bus that is NMEA 2000. Plus Vector BLF and
   ASC written by
   python-can's own writers covering CAN FD, 64-byte FD, error frames and a
   comma-decimal locale. One real log is then written out in all five formats
   and read back by every parser, which all have to agree about the same
   traffic.</p>
<p>A third run pushes the size rather than the variety: the 145,534-frame
   J1939 truck log and the 154,896-frame two-channel car log are merged into
   one 300,430-frame capture with 11-bit and 29-bit identifiers on three bus
   tags, and every stage is timed against a budget. 29 checks, all passing:
   parsing at 200,912 frames/s, the merged capture into the running window in
   8.1 s at 691 MB resident, the frame table refreshing in 65 ms, the sniffer
   folding the capture in 128 ms, and 145,534 frames written out and read back
   through five formats with every payload byte equal. Engine speed decodes to
   913 to 1762 rpm over 19,584 frames, which is external evidence rather than
   the code agreeing with itself. That run also found a real defect: the PGN
   scan crashed on any log carrying an active fault code, and this truck sends
   196 of them.</p>
<p>It found defects the older corpus could not reach. The openpilot DBC
   exporter wrote a bare 29-bit frame id, so every J1939 capture exported a
   file cantools refuses. The sniffer aged a loaded capture against wall-clock
   time, so every row expired the moment a file opened. The PGN decoder read
   the marine bus with J1939 tables and named nothing. All are fixed and
   pinned by tests. A narrated recording of the run is in the
   repository as <code>docs/canlab-realdata-validation.mp4</code>.</p>

{h2("Testing")}
<p>The suite runs headless:</p>
<pre><code>QT_QPA_PLATFORM=offscreen python -m pytest -q     # 673 passed</code></pre>
<p>Tests that need an optional dependency skip cleanly when it is absent: the
   MDF4 importer without <code>asammdf</code>, the transport tests without the
   MCP SDK, the Lua dissector without a Lua runtime.</p>
<p>It covers the log parsers against fixtures in the real formats, DBC encode
   and decode round trips through cantools, the ARM TX gate on every transmit
   path including that disarming mid-run stops a worker, ISO-TP and UDS wire
   format, DTC and PID decoding, the REST authentication and NaN-safe JSON, and
   an import smoke test of every tab.</p>

{h2("Limitations")}
<ul>
  <li><strong>Not validated on many real vehicles.</strong> Signal
      identification is heuristic. Verify every result before trusting it.</li>
  <li><strong>ARM TX covers the transmit features, not diagnostic reads.</strong>
      See <a href="safety.html#the-arm-tx-gate">the gate</a> for exactly which
      paths it covers.</li>
  <li><strong>ARXML export is experimental</strong> and is not validated
      against the AUTOSAR schema.</li>
  <li><strong>openpilot rlog import</strong> needs pycapnp plus the cereal
      schema; without them it raises rather than producing data.</li>
  <li><strong>MDF4</strong> needs <code>asammdf</code>. <strong>Vision
      OCR</strong> needs opencv, rapidocr and onnxruntime, which are heavy.</li>
  <li><strong>CAN FD</strong> parsing and decoding is partial in places.</li>
  <li><strong>The gateway needs two hardware channels.</strong></li>
  <li><strong>The prebuilt binary is Linux x86_64 and unsigned.</strong> No
      macOS or Windows build; run from source there.</li>
</ul>

{h2("Getting help")}
<p>Bugs and questions belong in <a href="{repo}/issues">the issue tracker</a>.
   A capture that reproduces the problem helps enormously; the sample format is
   plain CSV and easy to trim.</p>

{h2("Credits")}
<p>The calibration refinements in <code>core/calibrate_refine.py</code>,
   sentinel masking and scale snapping, are adapted from CSS Electronics'
   <a href="https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills">CAN
   bus reverse engineering skills</a> (MIT). The OEM checksum algorithms in
   <code>core/checksums.py</code> follow
   <a href="https://github.com/commaai/opendbc">commaai/opendbc</a> (MIT).</p>
<p>Built on python-can, cantools, PyQt6, pandas, NumPy and pyqtgraph.</p>
"""))
    pages.append((
        "cli.html",
        "Command line",
        "canlab-cli: the analysis without the window, and the capture kit "
        "for a small computer in a car.",
        f"""
<h1>Command line</h1>
<p class="lede">The analysis modules are Qt-free, so everything here runs
   without a display: in CI over a folder of drives, from a notebook, piped
   into something else, or on a Raspberry Pi in a car. A test asserts the
   process never imports Qt.</p>

<div class="video-card">
  <video controls preload="metadata" playsinline>
    <source src="canlab-cli-tour.mp4" type="video/mp4">
    Your browser cannot play this video.
    <a href="canlab-cli-tour.mp4">Download it instead</a>.
  </video>
  <div class="meta">
    <h3>The command line tour</h3>
    <p class="len">1:42</p>
    <p>Every command run for real against recordings this project did not
       produce, including a genuine capture: one process replays a real log
       onto a bus while <code>canlab-cli capture</code> records it and a mark
       is posted over HTTP.</p>
  </div>
</div>

{h2("The commands")}
<pre><code>canlab-cli ids      capture.csv                          # IDs, rates, moving bytes
canlab-cli detect   capture.csv --json out.json --dbc draft.dbc
canlab-cli decode   capture.csv --dbc signals.dbc --out decoded.csv
canlab-cli convert  capture.blf capture.csv              # csv, blf, asc, log
canlab-cli capture  --interface socketcan --channel can0 --keys b=brake</code></pre>
<p>Every command reads any format the application does. <code>detect</code>
   runs the counter and checksum sweep, entropy boundaries, bit-level flags,
   value-table inference and multiplexer detection, and can draft a DBC from
   what it found with overlapping claims resolved so the file loads in
   cantools. <code>convert</code> writes SavvyCAN's own CSV layout, so the
   result opens there as well as here. Nothing here transmits.</p>

{h2("Capture")}
<p><code>canlab-cli capture</code> is the capture kit: a headless, receive-only
   logger that writes SavvyCAN CSV segments as frames arrive, rotating by frame
   count (<code>--max-frames</code>, 200,000) or by time
   (<code>--max-seconds</code>, 600), keeps a <code>marks.json</code> updated
   on every mark, and when it stops folds the segments and marks into one
   <code>.canlab</code> project the desktop opens with every mark on the
   timeline.</p>
<pre><code>sudo ip link set can0 up type can bitrate 500000 listen-only on
canlab-cli capture --interface socketcan --channel can0 --bitrate 500000 \\
    --keys b=brake,h=horn --http 8765 --http-host 0.0.0.0 \\
    --token-file ~/.canlab/kit-token --gpio 17=brake --duration 3600</code></pre>
{table(["Marks from", "How"], [
    ["The keyboard",
     "<code>--keys b=brake</code>: on a terminal, <code>b</code> opens a "
     "<code>brake</code> interval and <code>b</code> closes it, <code>q</code> "
     "stops. Without <code>--keys</code>, or when stdin is not a terminal, type "
     "a label and Enter: <code>brake</code>, <code>brake off</code>, "
     "<code>brake point</code>."],
    ["HTTP",
     "<code>--http PORT</code> serves the same REST API as the window with "
     "<code>POST /mark</code>, <code>GET /frames</code> and "
     "<code>GET /status</code>, and without <code>/inject</code>. The token is "
     "generated on the first run and written to <code>--token-file</code>. "
     "Binding to anything but loopback is announced at start."],
    ["GPIO",
     "<code>--gpio 17=brake</code>: a switch between the pin and ground marks "
     "<code>brake</code> for as long as it is held, through gpiozero when it "
     "is installed. Without it the kit says so and records without switches."],
])}
<p><code>--adapter NAME</code> opens an adapter saved in the desktop's
   Settings, which are mirrored to <code>~/.canlab/adapters.json</code> for
   this purpose. Ctrl-C, SIGTERM or <code>--duration</code> stop the run
   cleanly: open marks are closed, the last segment is flushed, and the
   summary and the project path are printed. <code>--no-project</code> leaves
   the segments and marks only.</p>
<div class="note">
  <span class="callout-title">It never asks for privileges</span>
  <p>A SocketCAN link that is down gets the <code>ip link</code> command
     printed and exit code 2. The systemd unit in
     <code>canlab/examples/capture-kit/</code> brings <code>can0</code> up in
     listen-only mode with <code>CAP_NET_ADMIN</code> before the kit starts,
     and runs the kit itself as an ordinary user.</p>
</div>
<p>The kit has been exercised on python-can's virtual backend and a fake bus,
   with marks from stdin, HTTP and a fake gpiozero, not yet in a vehicle.</p>
"""))

    return pages
