/** Minimal SSE reader — fetch ReadableStream → onEvent callbacks. */
export async function readSSEStream(
  res: Response,
  onEvent: (event: string, data: string) => void,
): Promise<void> {
  if (!res.body) throw new Error("response has no body")
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  let event = "message"
  let dataLines: string[] = []

  const dispatch = () => {
    if (dataLines.length > 0 || event !== "message") {
      onEvent(event, dataLines.join("\n"))
    }
    event = "message"
    dataLines = []
  }

  const handleLine = (rawLine: string) => {
    const line = rawLine.replace(/\r$/, "")
    if (line === "") { dispatch(); return }
    if (line[0] === ":") return
    const colon = line.indexOf(":")
    const field = colon > 0 ? line.slice(0, colon) : line
    const val = colon > 0 ? line.slice(colon + 1).replace(/^ /, "") : ""
    if (field === "event") event = val
    else if (field === "data") dataLines.push(val)
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let nl: number
      while ((nl = buf.indexOf("\n")) >= 0) {
        handleLine(buf.slice(0, nl))
        buf = buf.slice(nl + 1)
      }
    }
    buf += decoder.decode()
    if (buf.length > 0) handleLine(buf)
    dispatch()
  } finally {
    try { reader.releaseLock() } catch { /* already released */ }
  }
}
