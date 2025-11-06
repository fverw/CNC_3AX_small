O<tool-setter> sub (measure tool with TS27R)
#<tool_number>=#1 (tool number)
#<tool_xy_diam>=#2 (~ XY plane tool diam, precise for ref tool, zero for no diam meas)
#<tool_z_diam>=#3 (=0 ~ Z plane tool diam, zero for same as XY plane -> single point and inserts)
#<tool_length>=#4 (~ tool length)
#<tool_rotate>=#5 (=1 rotate tool, ingored for ref tool)
#<update_tooltable>=#6 (=1 update tooltable)
#<ref_tool>=#7 (=0 reference tool)

(info: TS27r Toolsetter TLO)

;;;;;;;;;;;;;;;;;;;;;;
;; ini file params
;;
;; [TOOLSETTER]
;; X= tool setter G53 X
;; Y= tool setter G53 Y
;; Z= tool setter G53 Z
;; TOOLSETTER_DIAM=12.7
;; TOOLSETTER_HEIGHT=14
;; BACKOFF=1
;; Z_CLEARANCE=2
;; SAFE_HEIGHT=50
;; RAPID_FEED=1500
;; COARSE_FEED=500
;; COARSE_RPM=750
;; FINE_RPM=1500
;; PRECISION=0.005 # to calculate fine feed: feed (mm/min/rot) = precision * spindle rpm
;;;;;;;;;;;;;;;;;;;;;;
;; Global Vars - need to be manually added to linuxcnc.var once
;; 601 - reference tool position G53 Z
;; 602 - measured tool setter diameter
;;;;;;;;;;;;;;;;;;;;;;



o05 if [#<ref_tool> NE 1]
  o07 if [#601 EQ 0]
    (debug,reference tool z pos not set, please run cycle for ref tool)
    m2 (stop program)
  o07 endif
  
  o06 if [#602 EQ 0]
    (debug,tool setter diam not measured, please run cycle for ref tool)
    m2 (stop program)
  o06 endif
o05 else
  o51 if [#<tool_xy_diam> EQ 0]
    (debug,for measuring ref tool, please enter the ref tool precise diameter)
    m2 (stop program)
  o51 endif
o05 endif


;; preamble
g21 g17 g40 g80 g94 g90 g49 g92.1


;; load tool
T#<tool_number> M6

;;activate tool setter
M64 P0 

;;fetch ini params
#<tool_setter_x>=#<_ini[TOOLSETTER]X>
#<tool_setter_y>=#<_ini[TOOLSETTER]Y>
#<tool_setter_z>=#<_ini[TOOLSETTER]Z>
#<rapid_feed>=#<_ini[TOOLSETTER]RAPID_FEED>
#<coarse_feed>=#<_ini[TOOLSETTER]COARSE_FEED>
#<fine_rpm>=#<_ini[TOOLSETTER]FINE_RPM>
#<fine_feed>=[#<_ini[TOOLSETTER]PRECISION>*#<fine_rpm>*0.9]
#<coarse_rpm>=#<_ini[TOOLSETTER]COARSE_RPM>
#<probe_backoff>=#<_ini[TOOLSETTER]BACKOFF>
#<z_clearance>=#<_ini[TOOLSETTER]Z_CLEARANCE>
#<toolsetter_diam>=#<_ini[TOOLSETTER]TOOLSETTER_DIAM>
#<toolsetter_height>=#<_ini[TOOLSETTER]TOOLSETTER_HEIGHT>
#<safe_height>=#<_ini[TOOLSETTER]SAFE_HEIGHT>

;; calculate fine feed for requested precision 
o23 if [#<fine_feed> LE 1]
  #<fine_feed>=5
  (debug, calc. fine feed too small, suing #<fine_feed> mach units/min)
o23 else
  (debug, calc. fine feed=#<fine_feed> mach units/min)
o23 end if

g54 ; to be able to use #5221,2,3