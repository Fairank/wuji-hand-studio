"""Mainland-common one-hand number shapes, authored for Hand 2 SDK axis order.

These are reviewed kinematic candidates, not measured human data or force control.
Regional conventions differ. A fixed wrist does not reproduce arm orientation.
"""
REFERENCE='https://www.open.edu/openlearn/mod/oucontent/view.php?id=106502&section=6'
CONVENTION='mainland_common_v1'
DESCRIPTIONS={
    0:('握拳表示零','Closed fist for zero'),
    1:('食指伸直，其余收拢','Index extended; other fingers folded'),
    2:('食指、中指伸直并分开','Index and middle extended and separated'),
    3:('食指、中指、无名指伸直','Index, middle and ring extended'),
    4:('四指伸直，拇指收向掌内','Four fingers extended; thumb folded into palm'),
    5:('五指展开','All five fingers spread'),
    6:('拇指、小指伸出，其余三指收拢','Thumb and little finger extended; middle three folded'),
    7:('拇指、食指、中指指尖靠拢','Thumb, index and middle fingertips gathered'),
    8:('拇指、食指张开呈八字','Thumb and index extended apart'),
    9:('食指弯钩，其余手指收拢','Hooked index; other fingers folded'),
}

# All angles radians. S2 is lateral motion: outward is negative for index and
# positive for little finger in BOTH native Hand 2 models (not screen mirroring).
THUMB_TUCK=(.95,.10,1.10,.65)
THUMB_EXTEND=(-.60,-.30,.05,.05)
FAN=(0.,-.32,-.10,.13,.36)
SEVEN=(.930261,-.457870,.505524,.277122,
       .618314,.000384,.987046,.612804,
       .724803,-.039430,.916238,.552755,
       .85,0.,1.2,.9, .85,0.,1.2,.9)

def digit(number):
    if type(number) is not int or not 0<=number<=9:
        raise ValueError('Digit must be an integer from 0 to 9')
    if number==7:return list(SEVEN)
    q=list(THUMB_TUCK)+[.85,0.,1.2,.9]*4
    extended={0:(),1:(1,),2:(1,2),3:(1,2,3),4:(1,2,3,4),
              5:(1,2,3,4),6:(4,),8:(1,),9:(1,)}[number]
    for f in extended:q[4*f:4*f+4]=[.04,FAN[f],.04,.04]
    if number in (5,6,8):q[:4]=THUMB_EXTEND
    # Folded fingers occupy the palm for 0/1/2: the thumb rests outside them.
    # Four extended fingers leave room for the full inward thumb tuck of 4.
    if number in (0,1,2):q[:4]=[.30,-.30,.70,.50]
    if number==1:q[5]=-.10
    if number==8:q[5]=-.22
    if number==9:
        q[4:8]=[.08,-.14,1.30,.65]
        q[:4]=[.55,-.25,.70,.45]
    return q
