# place.tcl - Innovus 21.1, legacy UI
setMultiCpuUsage -localCpu 16
restoreDesign ../dbs/floorplan.enc.dat top_chip
setDesignMode -process 28
setPlaceMode -place_global_timing_effort high -place_global_cong_effort auto
placeDesign
timeDesign -preCTS -outDir reports/preCTS
optDesign -preCTS
saveDesign ../dbs/place.enc
