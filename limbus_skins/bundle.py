"""Unity bundles: read and write UnityFS, and give a modified bundle the stock CRC.

Since game version 1.117.1 Limbus checks each cached bundle against the CRC in its
catalog and re-downloads any that differ. Unity's CRC is CRC32 over the uncompressed
block data, and CRC32 can be steered to any value by choosing 4 bytes: forge() adds
4 bytes to the end of the first serialized file (past every object, so nothing reads
them) and writes the bundle uncompressed, so its CRC equals the stock one.
"""
import struct
import zlib


def _cstr(b, o):
    e = b.index(b"\0", o)
    return b[o:e].decode(), e + 1


def parse(raw):
    """UnityFS -> (header fields, block infos, node infos, uncompressed data)."""
    if not raw.startswith(b"UnityFS\0"):
        raise ValueError("not a UnityFS bundle")
    o = 8
    ver, = struct.unpack_from(">I", raw, o); o += 4
    uver, o = _cstr(raw, o)
    urev, o = _cstr(raw, o)
    _, csize, usize, flags = struct.unpack_from(">qIII", raw, o); o += 20
    if ver >= 7:
        o = (o + 15) & ~15
    if flags & 0x80:                     # blocks info at the end
        info_raw = raw[len(raw) - csize:]
        data_off = o
    else:
        info_raw = raw[o:o + csize]
        data_off = o + csize
    if flags & 0x200:
        data_off = (data_off + 15) & ~15
    info = _decompress(info_raw, flags & 0x3F, usize)
    p = 16
    n, = struct.unpack_from(">i", info, p); p += 4
    blocks = []
    for _ in range(n):
        u, c, f = struct.unpack_from(">IIH", info, p); p += 10
        blocks.append((u, c, f))
    m, = struct.unpack_from(">i", info, p); p += 4
    nodes = []
    for _ in range(m):
        off, sz, fl = struct.unpack_from(">qqI", info, p); p += 20
        name, p = _cstr(info, p)
        nodes.append([off, sz, fl, name])
    data, q = bytearray(), data_off
    for u, c, f in blocks:
        data += _decompress(raw[q:q + c], f & 0x3F, u)
        q += c
    return {"ver": ver, "uver": uver, "urev": urev, "flags": flags}, blocks, nodes, bytes(data)


def _decompress(b, kind, usize):
    if kind == 0:
        return b
    if kind in (2, 3):
        import lz4.block
        return lz4.block.decompress(b, uncompressed_size=usize)
    if kind == 1:
        import lzma
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1}]).decompress(b)
    raise ValueError(f"compression {kind} not handled")


def crc(raw):
    return zlib.crc32(parse(raw)[3]) & 0xFFFFFFFF


def build(hdr, nodes, data):
    """Uncompressed UnityFS: one block, blocks info right after the header."""
    info = bytearray(16)                                  # hash, unused by the loader
    info += struct.pack(">i", 1) + struct.pack(">IIH", len(data), len(data), 0x40)
    info += struct.pack(">i", len(nodes))
    for off, sz, fl, name in nodes:
        info += struct.pack(">qqI", off, sz, fl) + name.encode() + b"\0"
    head = b"UnityFS\0" + struct.pack(">I", hdr["ver"]) + hdr["uver"].encode() + b"\0" + hdr["urev"].encode() + b"\0"
    flags = 0x40                                          # info combined, not at end, uncompressed
    fixed_len = len(head) + 20
    pad = ((fixed_len + 15) & ~15) - fixed_len if hdr["ver"] >= 7 else 0
    total = fixed_len + pad + len(info) + len(data)
    out = head + struct.pack(">qIII", total, len(info), len(info), flags) + b"\0" * pad + bytes(info) + data
    return out


def forge(modded_raw, target):
    hdr, _, nodes, data = parse(modded_raw)
    # grow the first serialized file (the CAB, not .resS) by 4 bytes at its end
    first = min(range(len(nodes)), key=lambda k: (nodes[k][2] & 4 == 0, nodes[k][0]))
    off, sz = nodes[first][0], nodes[first][1]
    end = off + sz
    rest = data[end:]
    # crc32 is affine in the 4 inserted bytes: find them by solving over GF(2)
    base = zlib.crc32(data[:end] + b"\0\0\0\0" + rest) & 0xFFFFFFFF
    cols = []
    for bit in range(32):
        x = bytearray(4)
        x[bit // 8] = 1 << (bit % 8)
        cols.append((zlib.crc32(data[:end] + bytes(x) + rest) & 0xFFFFFFFF) ^ base)
    want = base ^ target
    sol = _solve(cols, want)
    x = bytearray(4)
    for bit in range(32):
        if sol >> bit & 1:
            x[bit // 8] |= 1 << (bit % 8)
    new = data[:end] + bytes(x) + rest
    for n in nodes:
        if n is nodes[first]:
            n[1] = sz + 4
        elif n[0] >= end:
            n[0] += 4
    out = build(hdr, nodes, new)
    if crc(out) != target:
        raise RuntimeError("could not give the bundle the stock CRC")
    return out


def _solve(cols, want):
    """GF(2): which subset of the 32 column vectors XORs to want (bitmask of columns)."""
    rows = [(c, 1 << i) for i, c in enumerate(cols)]
    basis = {}
    for v, m in rows:
        for b in range(31, -1, -1):
            if not v >> b & 1:
                continue
            if b in basis:
                v ^= basis[b][0]; m ^= basis[b][1]
            else:
                basis[b] = (v, m)
                break
    acc, m = want, 0
    for b in range(31, -1, -1):
        if acc >> b & 1:
            if b not in basis:
                raise ValueError("no solution")
            acc ^= basis[b][0]; m ^= basis[b][1]
    return m


