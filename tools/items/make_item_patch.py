"""Dev Menu - client patch for created items.

A 3.3.5 client only knows an item's type from its own Item.dbc. An item that is only on the server (made in
"Create an item") shows fine in tooltips, quest rewards and the Dev Menu, but in the bags it gets a red question
mark and the client does not treat it as a weapon or armour of its kind (no weapon in hand, wrong slot checks).

This tool reads every item of your world database that the client's Item.dbc does not have (or has with a
different type or look) and writes a client patch with those rows added:
  patch-ZZZZZZI.MPQ (or a free patch-Z/Y/X on stock clients)   put it in the client Data/ folder (players too), then restart the game

Run it again after you create or change items. Needs Python 3.8+ and the mysql command-line client
(or --rows: the Dev Menu's "Make client item patch" button runs it that way, no mysql needed).

Example:
  python make_item_patch.py --data "../WoW/Data" --user acore --password acore
  python make_item_patch.py --data "../WoW/Data" --defaults-file my.cnf --only-devmenu
"""
import argparse, glob, io, os, re, struct, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bake'))
import mpq

BS = chr(92)
ap = argparse.ArgumentParser(description='Add the server-only items to the client Item.dbc (fixes red icons and weapons that do not register).')
ap.add_argument('--data', default='auto', help='the client Data folder (with the MPQ archives); auto = the running game, the registry, then folders near --near')
ap.add_argument('--near', default='.', help='with --data auto: also look for a client in the folders around this one (the server passes its own)')
ap.add_argument('--out', help='where to write the patch (default: inside --data)')
ap.add_argument('--patch-name', default='auto', help='name of the client patch; auto = one that loads after the archive holding the client Item.dbc (patch-ZZZZZZI.MPQ on clients that load long names, else a free patch-Z/Y/X...)')
ap.add_argument('--only-devmenu', action='store_true', help='only the items of the Dev Menu range (--first .. --first+9998)')
ap.add_argument('--update-all', action='store_true', help='also overwrite stock rows the server has with another type or look (default: only add missing items and update Dev Menu items)')
ap.add_argument('--first', type=int, default=9400001, help='first Dev Menu item id (DevMenu.Items.FirstId; 0 there means 9400001)')
ap.add_argument('--mysql', default='mysql', help='mysql client executable')
ap.add_argument('--defaults-file', help='mysql option file with the login (instead of --user/--password)')
ap.add_argument('--host', default='127.0.0.1'); ap.add_argument('--port', default='3306')
ap.add_argument('--user', default='acore'); ap.add_argument('--password', default='acore')
ap.add_argument('--database', default='acore_world')
ap.add_argument('--rows', help='read the items from this tab-separated file instead of MySQL (entry class subclass sound material display invtype sheath); the Dev Menu button uses it')
ap.add_argument('--wait', type=int, default=0, help='if the game has the patch open, wait up to this many seconds for it to close')
ap.add_argument('--server-dbc', help='the server\'s dbc folder: with --data auto and no game client on this machine, its Item.dbc is used and the patch is written to --near/client_patch for you to hand out')
args = ap.parse_args()

MYSQL = [args.mysql] + (['--defaults-file=' + args.defaults_file] if args.defaults_file else
                        ['-h', args.host, '-P', str(args.port), '-u', args.user]) + ['-N', '-B', args.database]
ENV = dict(os.environ)
if not args.defaults_file:
    ENV['MYSQL_PWD'] = args.password


def sql(q):
    if args.rows:
        return [l.split('\t') for l in io.open(args.rows, encoding='utf-8').read().splitlines() if l.strip()]
    r = subprocess.run(MYSQL + ['-e', q], capture_output=True, text=True, env=ENV)
    if r.returncode:
        sys.exit('MySQL error: ' + r.stderr.strip())
    return [l.split('\t') for l in r.stdout.splitlines()]


# ------------------------------------------------------------------ client files (the archive that loads last wins)
def load_order(p):
    n = os.path.basename(p).lower()
    loc = os.path.basename(os.path.dirname(p)).lower() != 'data'
    m = re.match(r'patch(?:-[a-z]{4})?(?:-([a-z0-9]+))?\.mpq$', n)
    if not m:
        return (0, loc, n)
    s = m.group(1) or ''
    return (1, 0 if s == '' else 1 if s.isdigit() else 2, s, loc)


def is_data(d):
    try:
        names = {n.lower() for n in os.listdir(d)}
    except OSError:
        return False
    return bool(names & {'common.mpq', 'common-2.mpq', 'patch.mpq'})


def find_data(near):
    """The client Data folder: the running game first (the GM presses the button in it), then the registry, then nearby folders."""
    exes = []
    if os.name == 'nt':
        r = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Process | ForEach-Object { $_.Path }'],
                           capture_output=True, text=True)
        exes = [l.strip() for l in r.stdout.splitlines() if l.strip().lower().endswith('.exe')]
    for exe in exes:
        for d in (os.path.join(os.path.dirname(exe), 'Data'), os.path.join(os.path.dirname(os.path.dirname(exe)), 'Data')):
            if is_data(d):
                return d, 'running game'
    if os.name == 'nt':
        import winreg
        for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Blizzard Entertainment\World of Warcraft'),
                          (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Blizzard Entertainment\World of Warcraft')):
            try:
                with winreg.OpenKey(hive, key) as k:
                    d = os.path.join(winreg.QueryValueEx(k, 'InstallPath')[0], 'Data')
                if is_data(d):
                    return d, 'registry'
            except OSError:
                pass
    root = os.path.abspath(near)
    for _ in range(2):   # 2 folders above the server, 3 levels down
        root = os.path.dirname(root)
    found = []
    for base, dirs, _ in os.walk(root):
        if base[len(root):].count(os.sep) >= 3:
            dirs[:] = []
        for d in dirs:
            if d.lower() == 'data' and is_data(os.path.join(base, d)):
                found.append(os.path.join(base, d))
    if found:   # several clients: the one played last
        return max(found, key=lambda d: max(os.path.getmtime(os.path.join(d, n)) for n in os.listdir(d))), 'folders near the server'
    return None, None


def mpqs(d):   # case-insensitive, also on Linux (Wine clients)
    return [os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith('.mpq') and os.path.isfile(os.path.join(d, n))]


name = 'DBFilesClient' + BS + 'Item.dbc'
MARK = 'devmenu-item-patch.txt'   # inside our patch, so a later run knows it (and never reads it as the source)
remote = False   # no client here: build from the server's Item.dbc and hand the file out
if args.data == 'auto':
    args.data, how = find_data(args.near)
    if args.data:
        print('Client found (%s): %s' % (how, args.data))
    elif args.server_dbc and os.path.isfile(os.path.join(args.server_dbc, 'Item.dbc')):
        remote = True
        print('No game client on this machine: using the server\'s ' + os.path.join(args.server_dbc, 'Item.dbc'))
    else:
        sys.exit('No game client found: start the game, or set DevMenu.Items.ClientData (or --data) to its Data folder.')
elif not is_data(args.data):
    sys.exit('Not a game Data folder (no common.MPQ / patch.MPQ): ' + args.data)

arcs, own, skipped = [], None, []
if not remote:
    files = mpqs(args.data) + [p for s in os.listdir(args.data) if os.path.isdir(os.path.join(args.data, s)) for p in mpqs(os.path.join(args.data, s))]
    for p in sorted(files, key=load_order):
        if os.path.basename(p).lower() in (args.patch_name.lower(), 'patch-zzzzzzi.mpq'):
            own = own or p
            continue   # our own output from a previous run
        try:
            m = mpq.MPQ(p)
        except OSError as e:
            sys.exit('Cannot read %s (%s): a stale Item.dbc would be used, stopping.' % (p, e))
        except Exception:
            print('skipped (not an MPQ archive): ' + p)
            skipped.append(p)
            continue
        if m.has(MARK):
            own = p
            m.f.close()   # Windows: an open handle would block replacing or removing it later
        else:
            arcs.append((p, m))
src = src_path = None
for p, m in arcs:
    if m.has(name):
        src, src_path = m, p
if not src and not remote:
    sys.exit('No Item.dbc in the archives of ' + args.data)
for p in skipped:
    if load_order(p) > load_order(src_path):
        print('Warning: %s loads after %s but could not be read; if it has its own Item.dbc, this patch may undo it.' % (p, os.path.basename(src_path)))


def auto_name():
    """A name that loads after the archive with the client's Item.dbc. Stock 3.3.5 clients load only
    one-letter patches (patch-A..Z), so a long name is used only when the client already loads one."""
    if own and load_order(own) > load_order(src_path):
        return os.path.basename(own)
    suffix = load_order(src_path)[2] if load_order(src_path)[0] == 1 else ''
    if len(suffix) > 1:   # the client loads long names: e.g. patch-ZZZZZZF -> patch-ZZZZZZI
        for cand in ('patch-ZZZZZZI.MPQ', 'patch-%sI.MPQ' % suffix.upper()):
            if load_order(os.path.join(args.data, cand)) > load_order(src_path):
                return cand
    taken = {os.path.basename(p).lower() for p in mpqs(args.data)} - {os.path.basename(own or '').lower()}
    for c in 'ZYXWVUTSRQPONMLKJIHGFEDCBA':
        cand = 'patch-%s.MPQ' % c
        if cand.lower() not in taken and load_order(os.path.join(args.data, cand)) > load_order(src_path):
            return cand
    sys.exit('No free patch-A..Z name loads after %s: pass --patch-name.' % os.path.basename(src_path))


if remote:   # shortcut: patch-Z is a guess for clients we cannot see; DevMenu.Items.PatchName overrides it
    if args.patch_name == 'auto':
        args.patch_name = 'patch-Z.MPQ'
    args.out = args.out or os.path.join(os.path.abspath(args.near), 'client_patch')
    os.makedirs(args.out, exist_ok=True)
    data = io.open(os.path.join(args.server_dbc, 'Item.dbc'), 'rb').read()
else:
    if args.patch_name == 'auto':
        args.patch_name = auto_name()
    if load_order(os.path.join(args.data, args.patch_name)) < load_order(src_path):
        sys.exit('%s loads before %s, which has its own Item.dbc, so the game would ignore it. '
                 'Pick a later name with --patch-name (stock 3.3.5 clients only load one-letter names like patch-Z.MPQ).'
                 % (args.patch_name, os.path.basename(src_path)))
    data = src.read(name)

# Item.dbc (3.3.5): ID, Class, SubClass, SoundOverrideSubclass, Material, DisplayInfoID, InventoryType, SheathType
magic, n, fc, rs, ss = struct.unpack('<4s4I', data[:20])
if magic != b'WDBC' or fc != 8 or rs != 32:
    sys.exit('Unexpected Item.dbc layout (%s, %d fields): is this a 3.3.5 client?' % (magic, fc))
rows = {}
for i in range(n):
    r = list(struct.unpack_from('<8i', data, 20 + i * rs))
    rows[r[0]] = r
strings = data[20 + n * rs:]

where = 'WHERE entry BETWEEN %d AND %d' % (args.first, args.first + 9998) if args.only_devmenu else ''
items = sql('SELECT entry, class, subclass, SoundOverrideSubclass, Material, displayid, InventoryType, sheath FROM item_template ' + where)
added = changed = 0
for it in items:
    r = [int(v) for v in it]
    old = rows.get(r[0])
    if old == r:
        continue
    mine = args.first <= r[0] <= args.first + 9998
    if old is not None and not (mine or args.update_all):
        continue   # a stock item the client has its own way: leave it
    if old is None:
        added += 1
    else:
        changed += 1
    rows[r[0]] = r
if not added and not changed:
    print('The client already knows all %d items. Nothing to do.' % len(items))
    sys.exit(0)

body = b''.join(struct.pack('<8i', *rows[k]) for k in sorted(rows))
out_dbc = struct.pack('<4s4I', b'WDBC', len(rows), 8, 32, len(strings)) + body + strings
out = os.path.join(args.out or args.data, args.patch_name)
tmp = out + '.new'
try:
    mpq.write(tmp, {name: out_dbc, MARK: b'Made by the Dev Menu item patch tool (tools/items/make_item_patch.py).\r\n'})
except PermissionError:   # e.g. a game in Program Files and a server that is not run as administrator
    alt = os.path.join(os.path.abspath(args.near), 'client_patch')
    os.makedirs(alt, exist_ok=True)
    mpq.write(os.path.join(alt, args.patch_name), {name: out_dbc, MARK: b'Made by the Dev Menu item patch tool (tools/items/make_item_patch.py).\r\n'})
    sys.exit('No right to write in %s: copy %s there by hand, then restart the game.' % (os.path.dirname(out), os.path.join(alt, args.patch_name)))
assert mpq.MPQ(tmp).read(name) == out_dbc, 'archive check failed'
end = time.time() + args.wait
while True:   # a running game keeps the old patch open
    try:
        os.replace(tmp, out)
        break
    except PermissionError:
        if time.time() >= end:
            sys.exit('The game has %s open: close the game and run this again (the new patch is in %s).' % (out, tmp))
        time.sleep(5)
if own and not args.out and os.path.abspath(own).lower() != os.path.abspath(out).lower():
    try:   # our patch under its old name would still load, maybe after the new one
        os.remove(own)
        print('Removed the old patch ' + own)
    except OSError as e:
        print('Could not remove the old patch %s (%s): delete it by hand.' % (own, e))
print('Wrote ' + out + ' (give players the same file for their Data folder).')
if remote:   # last line: shown in game
    print('Client item patch made: %d items added, %d updated. Put %s in each game\'s Data folder, then restart the game.' % (added, changed, out))
else:
    print('Client item patch ready: %d items added, %d updated. Restart the game.' % (added, changed))
