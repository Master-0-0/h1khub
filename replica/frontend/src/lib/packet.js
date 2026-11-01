// Binary Packet framing compatible with HexHub 5.1.9's RPC client.
// Frame: u16 methodLen | u32 dataLen | u32 id | method | data, XOR'd by index.

const encoder = new TextEncoder()
const decoder = new TextDecoder()

export function encodePacket(method, id, data, xor = true) {
  const name = encoder.encode(method)
  const payload = data instanceof Uint8Array ? data : new Uint8Array(data || [])
  const frame = new Uint8Array(10 + name.length + payload.length)
  const view = new DataView(frame.buffer)
  view.setUint16(0, name.length)
  view.setUint32(2, payload.length)
  view.setUint32(6, id >>> 0)
  frame.set(name, 10)
  frame.set(payload, 10 + name.length)
  if (xor) {
    for (let i = 0; i < frame.length; i++) frame[i] ^= i & 0xff
  }
  return frame
}

export function decodePacket(buffer, xor = true) {
  const raw = buffer instanceof Uint8Array ? buffer.slice() : new Uint8Array(buffer)
  if (xor) {
    for (let i = 0; i < raw.length; i++) raw[i] ^= i & 0xff
  }
  if (raw.length < 10) return null
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
  const methodLen = view.getUint16(0)
  const dataLen = view.getUint32(2)
  const id = view.getUint32(6)
  const method = decoder.decode(raw.subarray(10, 10 + methodLen))
  const data = raw.subarray(10 + methodLen, 10 + methodLen + dataLen)
  return { method, id, data }
}

export function decodeJson(data) {
  try {
    return JSON.parse(decoder.decode(data || new Uint8Array()) || '{}')
  } catch {
    return {}
  }
}

// ---------------------------------------------------------------- protobuf
function readVarint(buf, pos) {
  let value = 0
  let shift = 0
  while (pos < buf.length) {
    const byte = buf[pos++]
    value += (byte & 0x7f) * 2 ** shift
    if (!(byte & 0x80)) break
    shift += 7
  }
  return { value, pos }
}

function readFields(buf) {
  const fields = []
  let pos = 0
  while (pos < buf.length) {
    const key = readVarint(buf, pos)
    pos = key.pos
    const field = Math.floor(key.value / 8)
    const wire = key.value & 7
    if (wire === 0) {
      const v = readVarint(buf, pos)
      pos = v.pos
      fields.push({ field, wire, value: v.value })
    } else if (wire === 2) {
      const len = readVarint(buf, pos)
      pos = len.pos
      const payload = buf.subarray(pos, pos + len.value)
      pos += len.value
      fields.push({ field, wire, value: payload })
    } else if (wire === 5) {
      pos += 4
    } else if (wire === 1) {
      pos += 8
    } else {
      break
    }
  }
  return fields
}

function decodePackedBools(buf) {
  const out = []
  let pos = 0
  while (pos < buf.length) {
    const v = readVarint(buf, pos)
    pos = v.pos
    out.push(v.value !== 0)
  }
  return out
}

/** Decode proto_model.TableDataResponse from docs/proto-recovered/table_data.proto. */
export function decodeTableData(bytes) {
  const out = { columns: [], rows: [], offset: 0, totalCount: 0, totalPage: 0 }
  for (const f of readFields(bytes)) {
    if (f.field === 1 && f.wire === 2) {
      for (const wf of readFields(f.value)) {
        if (wf.field === 1 && wf.wire === 2) {
          const col = { name: '', type: '' }
          for (const cf of readFields(wf.value)) {
            if (cf.field === 1 && cf.wire === 2) col.name = decoder.decode(cf.value)
            else if (cf.field === 2 && cf.wire === 2) col.type = decoder.decode(cf.value)
            else if (cf.field === 7 && cf.wire === 0) col.formatType = cf.value
            else if (cf.field === 8 && cf.wire === 2) col.fullType = decoder.decode(cf.value)
          }
          out.columns.push(col)
        } else if (wf.field === 2 && wf.wire === 2) {
          let nulls = []
          const cols = []
          for (const rf of readFields(wf.value)) {
            if (rf.field === 1) {
              nulls = rf.wire === 2 ? decodePackedBools(rf.value) : [rf.value !== 0]
            } else if (rf.field === 2 && rf.wire === 2) {
              cols.push(decoder.decode(rf.value))
            }
          }
          out.rows.push(cols.map((v, i) => (nulls[i] ? null : v)))
        }
      }
    } else if (f.field === 2 && f.wire === 0) out.offset = f.value
    else if (f.field === 3 && f.wire === 0) out.totalCount = f.value
    else if (f.field === 4 && f.wire === 0) out.totalPage = f.value
  }
  return out
}
