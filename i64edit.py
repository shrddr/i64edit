import argparse
import binascii
import io
import struct
import sys
import zlib
from shutil import copyfile

def hexdump(data):
    if data is None:
        return
    return binascii.b2a_hex(data).decode('utf-8')

def print_diff(b1, b2, width=16):
    if len(b1) != len(b2):
        print(f'different len {len(b1)} / {len(b2)}')
    for i in range(len(b1) // width):
        piece1 = b1[i*width:i*width+width]
        piece2 = b2[i*width:i*width+width]

        diff = ''
        for j in range(width):
            if piece1[j] == piece2[j]:
                diff += '  '
            else:
                diff += '!!'

        print(diff)
        print(hexdump(piece1), ' ', hexdump(piece2))


def unpack(fmt, data, start=0):
    fmt = '=' + fmt
    count = struct.calcsize(fmt)
    return struct.unpack(fmt, data[start:start + count])


def pack(fmt, data, start=0):
    fmt = '=' + fmt
    count = struct.calcsize(fmt)
    return struct.unpack(fmt, data[start:start + count])


def binary_search(a, k):
    first, last = 0, len(a)
    while first < last:
        mid = (first + last) >> 1
        if k < a[mid].key:
            last = mid
        else:
            first = mid + 1
    return first - 1

def remove_duplicates(items):
    return list(dict.fromkeys(items))

class IdaUnpacker:
    def __init__(self, data):
        self.data = data
        self.o = 0

    def eof(self):
        return self.o >= len(self.data)

    def next64(self):
        if self.eof():
            return None
        lo = self.next32()
        hi = self.next32()
        return (hi << 32) | lo

    def next64signed(self):
        val = self.next64()
        if val < 0x8000000000000000:
            return val
        return val - 0x10000000000000000

    def next32(self):
        if self.eof():
            return None
        byte = self.data[self.o:self.o+1]
        if byte == b'\xff':
            # a 32 bit value:
            # 1111 1111 xxxx xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            if self.o+5 > len(self.data):
                return None
            val, = struct.unpack_from(">L", self.data, self.o+1)
            self.o += 5
            return val
        elif byte < b'\x80':
            # a 7 bit value:
            # 0xxx xxxx
            self.o += 1
            val, = struct.unpack("B", byte)
            return val
        elif byte < b'\xc0':
            # a 14 bit value:
            # 10xx xxxx xxxx xxxx
            if self.o+2 > len(self.data):
                return None
            val, = struct.unpack_from(">H", self.data, self.o)
            self.o += 2
            return val&0x3FFF
        elif byte < b'\xe0':
            # a 29 bit value:
            # 110x xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            if self.o+4 > len(self.data):
                return None
            val, = struct.unpack_from(">L", self.data, self.o)
            self.o += 4
            return val&0x1FFFFFFF
        else:
            return None

class IdaPacker:
    def __init__(self):
        self.data = bytearray()

    def push64(self, val):
        lo = val & 0xFFFFFFFF
        hi = val >> 32
        self.push32(lo)
        self.push32(hi)

    def push64signed(self, val):
        if val < 0:
            val = val + 0x10000000000000000
        self.push64(val)

    def push32(self, val):
        if val < 0x80:
            # a 7 bit value:
            # 0xxx xxxx
            b = struct.pack("B", val)
        elif val < 0x4000:
            # a 14 bit value:
            # 10xx xxxx xxxx xxxx
            val |= 0x8000
            b = struct.pack(">H", val)
        elif val < 0x20000000:
            # a 29 bit value:
            # 110x xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            val |= 0x80000000
            b = struct.pack(">L", val)
        else:
            # a 32 bit value:
            # 1111 1111 xxxx xxxx xxxx xxxx xxxx xxxx xxxx xxxx
            b = struct.pack(">BI", 0xFF, val)
        self.data += b

class BytesReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.coverage = [False] * len(data)

    def tell(self):
        return self.pos

    def get_coverage(self):
        ret = ''
        state = -1
        count = 0
        for e in self.coverage:
            if e != state:
                if count:
                    ret += f'{state}*{count}, '
                state = e
                count = 1
            else:
                count += 1
        ret += f'{e}*{count}'
        return ret

    def read(self, count):
        end = self.pos + count
        if end > len(self.data):
            raise Exception("read overrun")
        ret = self.data[self.pos:end]
        self.coverage[self.pos:end] = [True] * (end - self.pos)
        self.pos = end
        return ret

    def reads(self, fmt):
        fmt = '=' + fmt
        count = struct.calcsize(fmt)
        data = self.read(count)
        ret = struct.unpack(fmt, data)
        if type(ret) == tuple and len(ret) == 1:
            return ret[0]
        return ret

    def seek(self, off):
        self.pos = off

    def modify(self, off, newbytes):
        end = off + len(newbytes)
        if end > len(self.data):
            raise NotImplementedError("read overrun")
        self.data[off:end] = newbytes

class BytesWriter:
    def __init__(self, other):
        self.data = bytearray(other)
        self.pos = 0
        self.coverage = [False] * len(self.data)

    def get_coverage(self):
        ret = ''
        state = -1
        count = 0
        for e in self.coverage:
            if e != state:
                if count:
                    ret += f'{state}*{count}, '
                state = e
                count = 1
            else:
                count += 1
        ret += f'{e}*{count}'
        return ret

    def write(self, bin):
        start = self.pos
        end = self.pos + len(bin)
        if end > len(self.data):
            raise NotImplementedError("write overrun")
        self.data[start:end] = bin
        self.pos = end
        self.coverage[start:end] = [True] * (end - start)

    def writes(self, fmt, *args):
        fmt = '=' + fmt
        self.write(struct.pack(fmt, *args))

    def seek(self, off):
        self.pos = off

class FileHandler:
    def __init__(self, filename):
        self.f = open(filename, "r+b")

    def read(self, count):
        bs = self.f.read(count)
        return bs

    def reads(self, fmt):
        fmt = '=' + fmt
        i = struct.calcsize(fmt)
        bs = self.f.read(i)
        ret = struct.unpack(fmt, bs)
        if type(ret) == tuple and len(ret) == 1:
            return ret[0]
        return ret

    def seek(self, off):
        self.f.seek(off, 0)

    def write(self, data):
        self.f.write(data)

    def writes(self, fmt, *args):
        fmt = '=' + fmt
        i = struct.calcsize(fmt)
        bs = struct.pack(fmt, *args)
        self.write(bs)

    def tell(self):
        return self.f.tell()

    def close(self):
        self.f.close()


class Entry:
    def __init__(self, i):
        self.i = i
        self.recofs = None
        self.keylen = None
        self.rawkey = None
        self.key = None
        self.vallen = None
        self.val = None

    def read_data(self, args, br: BytesReader, prevkey):
        br.seek(self.recofs)
        self.keylen = br.reads("H")
        self.rawkey = br.read(self.keylen)
        self.vallen = br.reads("H")
        self.val = br.read(self.vallen)

    def write_data(self, bw: BytesWriter):
        bw.seek(self.recofs)
        bw.writes("H", self.keylen)
        bw.write(self.rawkey)
        bw.writes("H", self.vallen)
        bw.write(self.val)

    def modify(self, args, acc):
        """Changes entry value, accumulates length changes,
        moves the entry backwards on page according to accumulated change"""
        if self.key.startswith(binascii.a2b_hex('2eff000000000000da53')):
            oldbytes = b'\x00' + args.rename[0].encode('utf-8')
            if self.val.startswith(oldbytes):
                newbytes = b'\x00' + args.rename[1].encode('utf-8')
                print(f'modify entry {self.i}')
                len_change = len(newbytes) - len(oldbytes)
                self.val = newbytes + self.val[len(oldbytes):]
                self.vallen += len_change
                acc += len_change
        self.recofs -= acc
        return acc


class IndexEntry(Entry):
    def __init__(self, i):
        super().__init__(i)
        self.npage = 0

    def read_head(self, br: BytesReader):
        self.npage, self.recofs = br.reads("LH")

    def write_head(self, bw: BytesWriter):
        bw.writes("LH", self.npage, self.recofs)

    def read_data(self, args, br: BytesReader, prevkey):
        super().read_data(args, br, prevkey)
        self.key = self.rawkey

class LeafEntry(Entry):
    def __init__(self, i):
        super().__init__(i)
        self.indent = 0
        self.unk = 0

    def read_head(self, br: BytesReader):
        self.indent, self.unk, self.recofs = br.reads("HHH")

    def write_head(self, bw: BytesWriter):
        bw.writes("HHH", self.indent, self.unk, self.recofs)

    def read_data(self, args, br, prevkey):
        super().read_data(args, br, prevkey)
        self.key = prevkey[:self.indent] + self.rawkey

class Page:
    def __init__(self, fh, i, pagesize):
        self.i = i
        self.fh = fh
        self.offset = fh.tell()
        br = BytesReader(fh.read(pagesize))

        self.br = br
        self.preceding, self.entrycount = br.reads("LH")
        # if self.entrycount == 0 or not args.rename:
        #     fh.fo.write(br.data)
        #     return

        self.entryType = LeafEntry
        if self.preceding:
            self.entryType = IndexEntry

        self.entries = []
        for i in range(self.entrycount):
            ent = self.entryType(i)
            ent.read_head(br)
            self.entries.append(ent)
        self.unk, self.datastart = br.reads("LH")
        self.free_bytes = self.datastart - br.tell()

        prevkey = b''
        for ent in self.entries:
            ent.read_data(args, br, prevkey)
            prevkey = ent.key
            if ent.recofs < self.datastart:
                raise NotImplementedError("unexpected entry data before page.datastart")

        self.modifications = 0

    def isindex(self):
        return self.preceding != 0

    def isleaf(self):
        return self.preceding == 0

    def getkey(self, ix):
        return self.entries[ix].key

    def find(self, key):
        """
        Searches pages for key, returns relation to key:

        recurse -> found a next level index page to search for key.
                   also returns the next level page nr
        gt -> found a value with a key greater than the one searched for.
        lt -> found a value with a key less than the one searched for.
        eq -> found a value with a key equal to the one searched for.
                   gt, lt and eq return the index for the key found.

        # for an index entry: the key is 'less' than anything in the page pointed to.
        """
        i = binary_search(self.entries, key)
        if i < 0:
            if self.isindex():
                return 'recurse', -1
            return 'gt', 0
        if self.entries[i].key == key:
            return 'eq', i
        if self.isindex():
            return 'recurse', i
        return 'lt', i

    def getpage(self, ix):
        """ For Indexpages, returns the page ptr for the specified entry """
        return self.preceding if ix < 0 else self.entries[ix].npage

    def getval(self, ix):
        """ For all page types, returns the value for the specified entry """
        return self.entries[ix].val

    def rebuild_modify(self, ix, newval):
        total_expand = 0
        for ent in sorted(self.entries, key=lambda e: e.recofs, reverse=True):
            # walk page entries data backwards and move left if expanded
            if ent.i == ix:
                total_expand += len(newval) - ent.vallen
                ent.val = newval
                ent.vallen = len(newval)
            ent.recofs -= total_expand

        self.free_bytes -= total_expand
        if self.free_bytes < 0:
            raise NotImplementedError("no more space in this page")

        self.datastart -= total_expand
        self.prepare_save()

    def rebuild_insert_entry(self, entry_i, entry_key, entry_val):
        ent = self.entryType(entry_i)
        self.entries.insert(entry_i, ent)
        self.entrycount += 1

        ent.key = entry_key

        if self.isleaf():
            prevkey = self.entries[entry_i - 1].key
            ent.indent = 0
            for a, b in zip(prevkey, entry_key):
                if a == b:
                    ent.indent += 1
                else:
                    break
            ent.rawkey = entry_key[ent.indent:]
        else:
            ent.rawkey = entry_key

        ent.keylen = len(ent.rawkey)
        ent.val = entry_val
        ent.vallen = len(entry_val)

        headlen = 6
        datalen = 2 + ent.vallen + 2 + ent.keylen
        entlen = headlen + datalen

        self.free_bytes -= entlen
        if self.free_bytes < 0:
            raise NotImplementedError("no more space in this page")

        ent.recofs = self.datastart - entlen
        self.datastart = ent.recofs
        self.prepare_save()

    def prepare_save(self):
        if self.modifications == 0:
            self.bw = BytesWriter(self.br.data)
        else:
            self.bw.seek(0)

        self.bw.writes("LH", self.preceding, self.entrycount)

        for ent in self.entries:
            ent.write_head(self.bw)

        self.bw.writes("LH", self.unk, self.datastart)
        for ent in self.entries:
            ent.write_data(self.bw)

        self.modifications += 1

    def save(self):
        self.fh.seek(self.offset)
        self.fh.write(self.bw.data)


class Cursor:
    """
    A Cursor object represents a position in the b-tree.

    It has methods for moving to the next or previous item.
    And methods for retrieving the key and value of the current position

    The position is represented as a list of (page, index) tuples
    """

    def __init__(self, db, stack):
        self.db = db
        self.stack = stack

    def next(self):
        """ move cursor to next entry """
        page, ix = self.stack.pop()
        if page.isleaf():
            # from leaf move towards root
            ix += 1
            while self.stack and ix == len(page.entries):
                page, ix = self.stack.pop()
                ix += 1
            if ix < len(page.entries):
                self.stack.append((page, ix))
        else:
            # from node move towards leaf
            self.stack.append((page, ix))
            page = self.db.readpage(page.getpage(ix))
            while page.isindex():
                ix = -1
                self.stack.append((page, ix))
                page = self.db.readpage(page.getpage(ix))
            ix = 0
            self.stack.append((page, ix))

    def prev(self):
        """ move cursor to the previous entry """
        page, ix = self.stack.pop()
        ix -= 1
        if page.isleaf():
            # move towards root, until non 'prec' item found
            while self.stack and ix < 0:
                page, ix = self.stack.pop()
            if ix >= 0:
                self.stack.append((page, ix))
        else:
            # move towards leaf
            self.stack.append((page, ix))
            while page.isindex():
                page = self.db.readpage(page.getpage(ix))
                ix = len(page.index) - 1
                self.stack.append((page, ix))

    def eof(self):
        return len(self.stack) == 0

    def getkey(self):
        """ return the key value pointed to by the cursor """
        page, ix = self.stack[-1]
        return page.getkey(ix)

    def getval(self):
        """ return the data value pointed to by the cursor """
        page, ix = self.stack[-1]
        return page.getval(ix)

    def getpageix(self):
        """ return the page and its entry pointed to by the cursor"""
        page, ix = self.stack[-1]
        return page, ix

    def __repr__(self):
        return "cursor:" + repr(self.stack)


def makekey(nodeid, tag, start):
    return struct.pack('>sQsQ', b'.', nodeid, tag.encode('utf-8'), start)

class IDBFile:
    def __init__(self, fh: FileHandler):
        self.fh = fh
        magic = fh.read(6)
        if not magic.startswith(b"IDA2"):
            raise Exception("invalid file format")

        self.head = list(fh.reads("QQLLHQQQ5LQL"))
        self.offsets = [self.head[_] for _ in (0, 1, 5, 6, 7, 13)]
        self.checksums = [self.head[_] for _ in (8, 9, 10, 11, 12, 14)]

    def move_section(self, i, amount):
        if self.offsets[i] == 0:
            return
        self.fh.seek(self.offsets[i])
        comp, size = self.fh.reads("BQ")
        # print(f"moving section {i} from {self.offsets[i]}..{self.offsets[i] + size} "
        #       f"to {self.offsets[i] + amount}..{self.offsets[i] + amount + size}")
        sect_data = self.fh.read(size)
        self.fh.seek(self.offsets[i] + amount)
        self.fh.writes("BQ", comp, size)
        self.fh.write(sect_data)
        self.offsets[i] += amount

    def write_head(self):
        self.fh.seek(6)
        for o, p in enumerate([0, 1, 5, 6, 7, 13]):
            self.head[p] = self.offsets[o]
        for o, p in enumerate([8, 9, 10, 11, 12, 14]):
            self.head[p] = self.checksums[o]
        self.fh.writes("QQLLHQQQ5LQL", *self.head)


class ID0:
    def __init__(self, idb: IDBFile):
        self.idb = idb
        ofh = idb.fh
        ofh.seek(self.idb.offsets[0])
        self.ofh = ofh
        self.comp, self.size = ofh.reads("BQ")
        self.modified = False
        if self.comp == 0:
            self.fs = ofh
        elif self.comp == 2:
            self.fs = io.BytesIO(zlib.decompress(ofh.read(self.size), 15))
        else:
            raise NotImplementedError("unsupported compression type")

        self.start = self.fs.tell()
        btreedata = self.fs.read(64)
        self.firstfree, self.pagesize, self.firstindex, \
            self.reccount, self.pagecount = unpack("LHLLL", btreedata)
        if not btreedata[19:].startswith(b"B-tree v2"):
            raise NotImplementedError("unknown b-tree format")

        self.edits = {}

    def readpage(self, nr):
        """ reads from file """
        self.fs.seek(self.start + nr * self.pagesize)
        return Page(self.fs, nr, self.pagesize)

    def namekey(self, name):
        if type(name) == int:
            return struct.pack("sBQ", b'N', 0, name)
        return b'N' + name.encode('utf-8')

    def nodeByName(self, name):
        cur = self.find('eq', self.namekey(name))
        if cur:
            return struct.unpack('Q', cur.getval())[0]

    def find(self, request, key):
        # descend tree to leaf nearest to the `key`
        page = self.readpage(self.firstindex)
        stack = []
        while True:
            response, ix = page.find(key)
            stack.append((page, ix))
            if len(stack) == 256:
                raise Exception("b-tree corrupted")
            if response != 'recurse':
                break
            page = self.readpage(page.getpage(ix))

        cursor = Cursor(self, stack)

        # now correct for what was actually asked.
        if response == request:
            pass
        elif request == 'eq' and response != 'eq':
            return None
        elif request in ('ge', 'le') and response == 'eq':
            pass
        elif request in ('gt', 'ge') and response == 'lt':
            cursor.next()
        elif request == 'gt' and response == 'eq':
            cursor.next()
        elif request in ('lt', 'le') and response == 'gt':
            cursor.prev()
        elif request == 'lt' and response == 'eq':
            cursor.prev()

        return cursor

    def blob(self, nodeid, tag, start=0, end=0xFFFFFFFF):
        """ returns combined data between multiple entries and all affected pages"""

        startkey = makekey(nodeid, tag, start)
        endkey = makekey(nodeid, tag, end)
        cur = self.find('ge', startkey)
        data = b''
        affected = []
        while cur.getkey() <= endkey:
            page, entry_i = cur.getpageix()
            affected.append((page.i, entry_i))
            chunk = page.entries[entry_i].val
            data += chunk
            cur.next()
        affected = remove_duplicates(affected)
        return data, affected

    def save(self):
        if not self.modified:
            return
        print('saving target file...')
        for page in self.edits.values():
            print('saving page', page.i)
            page.save()  # if not compressed writes directly to file
        if self.comp:
            self.fs.seek(0)
            print('deflating...')
            compressed = zlib.compress(self.fs.read())
            expand = len(compressed) - self.size
            if expand > 0:
                print('moving sections...')
                for i in range(len(self.idb.offsets) - 1, 0, -1):
                    self.idb.move_section(i, expand)
                self.idb.write_head()

            self.size = len(compressed)
            self.ofh.seek(self.idb.offsets[0])
            self.ofh.writes("BQ", self.comp, self.size)
            self.ofh.write(compressed)


def processfile(args):
    fh = FileHandler(args.target)
    idb = IDBFile(fh)
    id0 = ID0(idb)

    fdl = FuncDirList(id0)
    if args.list:
        fdl.print()

    if args.check:
        fdl.checktree()

    if args.rename:
        fdl.rename(args.rename)

    if args.move:
        fdl.move(args.move)

    if args.insert:
        fdl.insert(args.insert)

    id0.save()
    fh.close()


class FuncDirList:
    def __init__(self, id0):
        self.id0 = id0
        # same as: idbtool.py a/a.i64 --query "$ dirtree/funcs;S;0"
        self.rootnode = id0.nodeByName('$ dirtree/funcs')
        if not self.rootnode:
            raise ValueError('no function tree entry')

        # same as: idbtool.py a/a.i64 --query "$ dirtree/funcs;B;0"
        overview, self.ov_affected = id0.blob(self.rootnode, 'B', 0, 0xFFFF)
        p = IdaUnpacker(overview)
        self.first_dir = p.next32()
        self.dircount = p.next32()

        # TODO: decypher
        self.sort_info = []
        while not p.eof():
            self.sort_info.append(p.next32())

        self.dirs = {}

        for i in range(self.dircount):
            if 0 < i < self.first_dir:
                i = self.first_dir

            start = i * 0x10000
            end = start + 0xFFFF
            # same as: idbtool.py a/a.i64 --query "$ dirtree/funcs;S;65536"
            data, affected = id0.blob(self.rootnode, 'S', start, end)
            # print(f'funcdir {i} located at: {affected}')
            if data == b'':
                print(f"funcdir {i} data empty")
                continue
            self.dirs[i] = FuncDir(id0, i, data, affected)

        i = self.dircount
        start = i * 0x10000
        end = start + 0xFFFF
        data, affected = id0.blob(self.rootnode, 'S', start, end)
        if data != b'':
            print("there are extra dir entries")


    def print(self):
        for d in self.dirs.values():
            d.print()

    def rename(self, args):
        for d in self.dirs:
            d.rename(args)

    def move(self, args):
        i, newparent = args
        oldparent = self.dirs[i].parent
        self.dirs[oldparent].subdirs.remove(i)
        self.dirs[oldparent].apply_edit()
        self.dirs[newparent].subdirs.append(i)
        self.dirs[newparent].apply_edit()
        self.dirs[i].parent = newparent
        self.dirs[i].apply_edit()

    def insert(self, args):
        i, newparent = args
        if i in self.dirs:
            raise ValueError(f"dir {i} already exists")

        left_siblings = [v for k, v in self.dirs.items() if k < i]
        if not left_siblings:
            raise ValueError("no funcdir entries. need at least one sibling to attach to")
        left_sibling = left_siblings[-1]

        # new entry will be the next after left sibling
        page_i, entry_i = left_sibling.affected[-1]
        affected = [(page_i, entry_i+1)]

        d = FuncDir(self.id0, i, None, affected)
        self.dirs[i] = d
        d.name = f'newfolder_{i}'
        d.parent = newparent
        entry_key = makekey(self.rootnode, 'S', i * 0x10000)
        d.apply_insert(entry_key)

        if i not in self.dirs[newparent].subdirs:
            self.dirs[newparent].subdirs.append(i)
            self.dirs[newparent].apply_edit()

        self.dircount = len(self.dirs)
        print("applying overview")
        if len(self.ov_affected) > 1:
            raise NotImplementedError("overview data spans across multiple Entries")
        for page_i, entry_i in self.ov_affected:
            print(f'  affected page {page_i} entry {entry_i}')
            p = IdaPacker()
            p.push32(self.first_dir)
            p.push32(self.dircount)
            for s in self.sort_info:
                p.push32(s)
            newdata = p.data

            if page_i in self.id0.edits:
                page = self.id0.edits[page_i]
                page.rebuild_modify(entry_i, newdata)
            else:
                page = self.id0.readpage(page_i)
                page.rebuild_modify(entry_i, newdata)
                self.id0.edits[page_i] = page


    def checktree(self):
        errcode = 0
        # check if parent of A has A as subdir
        for i, d in self.dirs.items():
            if i == 0:
                continue
            if d.parent not in self.dirs:
                print(f'dir {i} has parent {d.parent} but {d.parent} is not in tree')
                errcode = 1
                continue
            subdirs = self.dirs[d.parent].subdirs
            if i not in subdirs:
                print(f'dir {i} has parent {d.parent} but {d.parent} has no subdir {i}')
                errcode = 1

        # check if subdirs of A have A as parent
        for i, d in self.dirs.items():
            for subdir in d.subdirs:
                if subdir not in self.dirs:
                    print(f'dir {i} has subdir {subdir} but {subdir} is not in tree')
                    errcode = 1
                    continue
                subdir_parent = self.dirs[subdir].parent
                if subdir_parent != i:
                    print(f'dir {i} has subdir {subdir} but {subdir} parent is {subdir_parent}')
                    errcode = 1

        print('check complete')
        sys.exit(errcode)

class FuncDir:
    def __init__(self, id0: ID0, i, data, affected):
        self.id0 = id0
        self.i = i
        self.affected = affected

        self.name = ''
        self.parent = 0
        self.unk32 = 0
        self.subdirs = []
        self.funcs = []

        if data:
            self.parse(data)

    def parse(self, data):
        terminate = data.find(b'\0', 1)
        self.name = data[1:terminate].decode('utf-8')

        p = IdaUnpacker(data[terminate + 1:])
        self.parent = p.next64()
        self.unk32 = p.next32()
        subdir_count = p.next32()

        self.subdirs = []
        while subdir_count:
            subdir_id = p.next64signed()
            if self.subdirs:
                subdir_id = self.subdirs[-1] + subdir_id
            self.subdirs.append(subdir_id)
            subdir_count -= 1

        func_count = p.next32()
        self.funcs = []
        while func_count:
            func_id = p.next64signed()
            if self.funcs:
                func_id = self.funcs[-1] + func_id
            self.funcs.append(func_id)
            func_count -= 1

        if not p.eof():
            raise Exception('not EOF after dir parsed')

    def print(self):
        print("dir %d = %s" % (self.i, self.name))
        print(" parent = %d" % self.parent)
        print(" subdirs:")
        for subdir in self.subdirs:
            print("  %d" % subdir)
        # print("  functions:")
        # for func in funcs:
        #     print("  ", end="")
        #     name = id0.name(func)
        #     if name:
        #         print("%x %s" % (func, name))

    def rename(self, args):
        newname = self.name.replace(*args)
        if newname != self.name:
            self.name = newname
            self.apply_edit()

    def pack(self):
        name = b'\x00' + self.name.encode('utf-8') + b'\x00'
        p = IdaPacker()
        p.push64(self.parent)
        p.push32(self.unk32)
        p.push32(len(self.subdirs))

        if len(self.subdirs):
            baseid = self.subdirs[0]
            p.push64(baseid)
            for subdir in self.subdirs[1:]:
                relative = subdir - baseid
                baseid = subdir
                p.push64signed(relative)

        p.push32(len(self.funcs))

        if len(self.funcs):
            baseofs = self.funcs[0]
            p.push64(baseofs)
            for func in self.funcs[1:]:
                relative = func - baseofs
                baseofs = func
                p.push64signed(relative)

        newdata = name + p.data
        return newdata

    def apply_edit(self):
        print(f'applying FuncDir {self.i}')
        if len(self.affected) > 1:
            raise NotImplementedError("dir data spans across multiple Entries")
        for page_i, entry_i in self.affected:
            print(f'  affected page {page_i} entry {entry_i}')
            newdata = self.pack()
            if page_i in self.id0.edits:
                page = self.id0.edits[page_i]
                page.rebuild_modify(entry_i, newdata)
            else:
                page = self.id0.readpage(page_i)
                page.rebuild_modify(entry_i, newdata)
                self.id0.edits[page_i] = page
        self.id0.modified = True

    def apply_insert(self, entry_key):
        print(f'applying inserted FuncDir {self.i}')
        page_i, entry_i = self.affected[0]
        print(f'  affected page {page_i} entry {entry_i}')

        entry_val = self.pack()
        if page_i in self.id0.edits:
            page = self.id0.edits[page_i]
            page.rebuild_insert_entry(entry_i, entry_key, entry_val)
        else:
            page = self.id0.readpage(page_i)
            page.rebuild_insert_entry(entry_i, entry_key, entry_val)
            self.id0.edits[page_i] = page

        self.id0.modified = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Modifies funcdir tree data inside a .i64 file',
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""
Examples:

  i64edit target.i64 --list --check
  i64edit target.i64 --rename BadDirName GoodDirName
  i64edit target.i64 --move 12 14
  i64edit --copyfrom in.i64 out.i64 --insert 4 1
""")
    parser.add_argument("--copyfrom", metavar='filename', help='make a copy before modifying')
    parser.add_argument("target", help='IDA database to modify')
    parser.add_argument('--list', action='store_true', help='print funcdir tree')
    parser.add_argument('--check', action='store_true', help='check consistency (exit code 1 = have issues)')
    parser.add_argument('--rename', nargs=2, help='string search and replace in folder names', metavar=('from', 'to'))
    parser.add_argument('--move', nargs=2, type=int, help='move folder #i to a new parent #j', metavar=('i', 'j'))
    parser.add_argument('--insert', nargs=2, type=int, help='create folder #i with parent #j', metavar=('i', 'j'))
    args = parser.parse_args()

    if args.copyfrom:
        copyfile(args.copyfrom, args.target)
    processfile(args)
