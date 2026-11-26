/****************************************************************************
 * cadence_mcp.il -- helper procedures for the cadence-mcp server
 *
 * Loaded automatically by the MCP server (live via skillbridge, or in batch
 * mode by "virtuoso -nograph"). It can also be loaded from .cdsinit.
 *
 * Target releases: IC6.1.7, IC6.1.8, ICADVM18.1/20.1, IC23.1, IC25.1.
 * Only core SKILL plus long-standing dd*/db*/sch* functions are used; any
 * release-specific function is probed with isCallable() before use.
 * The core helpers (CdsMcpEval, CdsMcpJson...) also run in Allegro SKILL.
 *
 * All public names start with "CdsMcp".
 ****************************************************************************/

/* ---------------------------------------------------------------- JSON */

procedure(CdsMcpJsonStr(s)
  let((out c n)
    unless(stringp(s) s = sprintf(nil "%L" s))
    n = strlen(s)
    out = list("\"")
    for(i 1 n
      c = substring(s i 1)
      cond(
        ((c == "\"") out = cons("\\\"" out))
        ((c == "\\") out = cons("\\\\" out))
        ((c == "\n") out = cons("\\n" out))
        ((c == "\t") out = cons("\\t" out))
        (t out = cons(c out))
      )
    )
    out = cons("\"" out)
    buildString(reverse(out) "")
  )
)

procedure(CdsMcpRaw(jsonText)
  list('CdsMcpRaw jsonText)
)

procedure(CdsMcpBool(x)
  if(x then CdsMcpRaw("true") else CdsMcpRaw("false"))
)

procedure(CdsMcpJson(x)
  cond(
    ((null x) "null")
    ((eq x t) "true")
    ((stringp x) CdsMcpJsonStr(x))
    ((fixp x) sprintf(nil "%d" x))
    ((floatp x) sprintf(nil "%.15g" x))
    ((symbolp x) CdsMcpJsonStr(get_pname(x)))
    (and(listp(x) eq(car(x) 'CdsMcpRaw)) cadr(x))
    ((listp x) strcat("[" buildString(mapcar('CdsMcpJson x) ",") "]"))
    (t CdsMcpJsonStr(sprintf(nil "%L" x)))
  )
)

/* CdsMcpObj("key1" value1 "key2" value2 ...) -> JSON object */
procedure(CdsMcpObj(@rest kv)
  let((parts k v)
    while(kv
      k = car(kv)
      v = cadr(kv)
      kv = cddr(kv)
      parts = cons(strcat(CdsMcpJsonStr(k) ":" CdsMcpJson(v)) parts)
    )
    CdsMcpRaw(strcat("{" buildString(reverse(parts) ",") "}"))
  )
)

procedure(CdsMcpTake(l n)
  let((res (i 0))
    while(and(l i < n)
      res = cons(car(l) res)
      l = cdr(l)
      i++
    )
    reverse(res)
  )
)

/* Elements offset+1 .. offset+n of l (pagination). */
procedure(CdsMcpSlice(l offset n)
  CdsMcpTake(nthcdr(offset l) n)
)

/* ------------------------------------------------------- evaluation core */

/* Evaluate a string of SKILL code, capturing printed output and errors.
   Returns list(status output value kind) with status "ok" or "error". */
procedure(CdsMcpEval(code)
  let((port res ok out)
    port = outstring()
    ok = errset(
      let(((poport port) (woport port))
        res = evalstring(strcat("{\n" code "\n}"))
        when(isCallable('getWarn)        ; flush pending warnings into woport
          warn("")
          getWarn()
        )
      )
    )
    out = getOutstring(port)
    close(port)
    if(ok then
      list("ok" out
           if(stringp(res) then res else sprintf(nil "%L" res))
           get_pname(type(res)))
    else
      list("error" out sprintf(nil "%L" errset.errset) "error")
    )
  )
)

procedure(CdsMcpReadFile(path)
  let((port line lines)
    port = infile(path)
    unless(port error("CdsMcp: cannot read %s" path))
    while(gets(line port)
      lines = cons(line lines)
    )
    close(port)
    buildString(reverse(lines) "")
  )
)

procedure(CdsMcpEvalFile(path)
  let((code)
    if(errset(code = CdsMcpReadFile(path)) then
      CdsMcpEval(code)
    else
      list("error" "" sprintf(nil "%L" errset.errset) "error")
    )
  )
)

/* Batch mode: write the CdsMcpEval result to a marker file read by Python. */
procedure(CdsMcpWriteResult(path r)
  let((p)
    p = outfile(path "w")
    when(p
      fprintf(p "CDSMCP_STATUS %s\n" nth(0 r))
      fprintf(p "CDSMCP_KIND %s\n" nth(3 r))
      fprintf(p "CDSMCP_OUTPUT_BEGIN\n")
      fprintf(p "%s" nth(1 r))
      fprintf(p "\nCDSMCP_OUTPUT_END\n")
      fprintf(p "CDSMCP_VALUE_BEGIN\n")
      fprintf(p "%s" nth(2 r))
      fprintf(p "\nCDSMCP_VALUE_END\n")
      close(p)
    )
    t
  )
)

/* ------------------------------------------------------- introspection */

procedure(CdsMcpCallable(name)
  CdsMcpObj("name" name "callable" CdsMcpBool(isCallable(stringToSymbol(name))))
)

procedure(CdsMcpVersionInfo()
  let((probes)
    probes = list(
      "maeOpenSetup" "maeRunSimulation"        ; ADE Explorer/Assembler (maestro)
      "axlGetWindowSession"                    ; axl* session API (present in all releases)
      "sevSession" "asiGetSession"             ; ADE L (sev*), ADE internals (asi*)
      "schCheck" "dbOpenCellViewByType" "sklint"
      "geGetEditCellView" "hiGraphicMode" "pcreCompile" "dbGetDatabaseType")
    CdsMcpObj(
      "version" getVersion()
      "subVersion" getVersion(t)
      "dbType" if(isCallable('dbGetDatabaseType) then dbGetDatabaseType() else "unknown")
      "graphic" CdsMcpBool(and(isCallable('hiGraphicMode) hiGraphicMode()))
      "cdsHome" getShellEnvVar("CDSHOME")
      "workingDir" getWorkingDir()
      "lp64" CdsMcpBool(car(errset(system.LP64)))
      "features" mapcar('CdsMcpCallable probes)
    )
  )
)

procedure(CdsMcpListLibs()
  mapcar(lambda((lib) CdsMcpObj("name" lib~>name "path" lib~>readPath))
         ddGetLibList())
)

/* Cells sorted by name, optionally filtered by a rexMatchp pattern, one page at a time. */
procedure(CdsMcpListCells(libName @optional pattern (offset 0) (limit 50))
  let((lib names)
    lib = ddGetObj(libName)
    unless(lib error("CdsMcp: library %s is not defined in cds.lib" libName))
    names = sort(setof(n lib~>cells~>name or(null(pattern) rexMatchp(pattern n))) 'alphalessp)
    CdsMcpObj("library" libName "path" lib~>readPath "total" length(names)
              "cells" mapcar(lambda((name) CdsMcpObj("name" name
                                                     "views" ddGetObj(libName name)~>views~>name))
                             CdsMcpSlice(names offset limit)))
  )
)

procedure(CdsMcpInstInfo(inst withParams)
  CdsMcpObj(
    "name" inst~>name
    "lib" inst~>libName "cell" inst~>cellName "view" inst~>viewName
    "params" if(withParams then
                 mapcar(lambda((p) list(p~>name p~>value)) inst~>prop)
               else nil)
  )
)

/* offset/maxItems page the terminals, instances, nets and layers lists. */
procedure(CdsMcpCvSummary(cv offset maxItems)
  let((isSch)
    isSch = (cv~>cellViewType == "schematic")
    CdsMcpObj(
      "lib" cv~>libName "cell" cv~>cellName "view" cv~>viewName
      "cellViewType" cv~>cellViewType
      "mode" cv~>mode
      "bBox" cv~>bBox
      "modifiedButNotSaved" CdsMcpBool(cv~>modifiedButNotSaved)
      "instanceCount" length(cv~>instances)
      "netCount" length(cv~>nets)
      "terminalCount" length(cv~>terminals)
      "shapeCount" length(cv~>shapes)
      "terminals" mapcar(lambda((tm) CdsMcpObj("name" tm~>name "direction" tm~>direction))
                         CdsMcpSlice(cv~>terminals offset maxItems))
      "instances" mapcar(lambda((i) CdsMcpInstInfo(i isSch))
                         CdsMcpSlice(cv~>instances offset maxItems))
      "nets" CdsMcpSlice(cv~>nets~>name offset maxItems)
      "layers" if(isSch then nil else CdsMcpSlice(cv~>layerPurposePairs~>layerName offset maxItems))
    )
  )
)

procedure(CdsMcpCellViewInfo(libName cellName viewName @optional (offset 0) (maxItems 50))
  let((cv info)
    cv = dbOpenCellViewByType(libName cellName viewName nil "r")
    unless(cv error("CdsMcp: cannot open %s/%s/%s" libName cellName viewName))
    info = errset(CdsMcpCvSummary(cv offset maxItems))
    dbClose(cv)
    unless(info error("CdsMcp: failed to summarise %s/%s/%s: %L" libName cellName viewName errset.errset))
    car(info)
  )
)

procedure(CdsMcpCurrentCellView(@optional (offset 0) (maxItems 50))
  let((cv)
    unless(isCallable('geGetEditCellView) error("CdsMcp: no graphical editor in this session"))
    cv = geGetEditCellView()
    unless(cv error("CdsMcp: no cellview is open in the current window"))
    CdsMcpCvSummary(cv offset maxItems)
  )
)

/* Needs access level "write" on the Python side (opens in append mode). */
procedure(CdsMcpSchCheck(libName cellName viewName @optional save)
  let((cv res)
    cv = dbOpenCellViewByType(libName cellName viewName nil "a")
    unless(cv error("CdsMcp: cannot open %s/%s/%s for append" libName cellName viewName))
    res = schCheck(cv)
    when(save dbSave(cv))
    dbClose(cv)
    CdsMcpObj("lib" libName "cell" cellName "view" viewName
              "errors" car(res) "warnings" cadr(res) "saved" CdsMcpBool(save))
  )
)

printf("cadence-mcp SKILL helpers loaded\n")
t
