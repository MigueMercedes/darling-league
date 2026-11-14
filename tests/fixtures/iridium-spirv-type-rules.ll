; Synthetic AIR kernel for tests/test_iridium_spirv_type_rules.py. Each value exercises one
; SPIR-V type rule: narrow integer constants in an i8 array and an i16 add, a constant GEP
; into a Private global aggregate, i1 equality (scalar and vector), sign- and zero-extended
; i1 (scalar and vector) and truncation to i1 (scalar and vector).
target datalayout = "e-p:64:64:64-i1:8:8-i8:8:8-i16:16:16-i32:32:32-i64:64:64-f32:32:32-f64:64:64-v16:16:16-v24:32:32-v32:32:32-v48:64:64-v64:64:64-v96:128:128-v128:128:128-v192:256:256-v256:256:256-v512:512:512-v1024:1024:1024-n8:16:32"
target triple = "air64-apple-macosx14.0.0"

@bytes = internal constant [2 x i8] c"\01\02"
@pair = internal constant { i32, [2 x i32] } { i32 7, [2 x i32] [i32 3, i32 4] }

define void @probe() {
entry:
  %b = load i8, ptr getelementptr inbounds ([2 x i8], ptr @bytes, i64 0, i64 1)
  %arr = getelementptr inbounds [2 x i32], ptr getelementptr inbounds ({ i32, [2 x i32] }, ptr @pair, i64 0, i32 1), i64 0, i64 1
  %x = load i32, ptr %arr
  %h = trunc i32 %x to i16
  %h2 = add i16 %h, 3
  %a = icmp ult i32 %x, 5
  %c = icmp ult i32 %x, 9
  %eq = icmp eq i1 %a, %c
  %ne = icmp ne i1 %a, %c
  %s = sext i1 %eq to i32
  %va0 = insertelement <2 x i1> poison, i1 %a, i32 0
  %va = insertelement <2 x i1> %va0, i1 %c, i32 1
  %vb0 = insertelement <2 x i1> poison, i1 %ne, i32 0
  %vb = insertelement <2 x i1> %vb0, i1 %eq, i32 1
  %veq = icmp eq <2 x i1> %va, %vb
  %vs = sext <2 x i1> %veq to <2 x i32>
  %vz = zext <2 x i1> %veq to <2 x i32>
  %t = trunc i32 %x to i1
  %tz = zext i1 %t to i32
  %vt = trunc <2 x i32> %vz to <2 x i1>
  %vtz = zext <2 x i1> %vt to <2 x i16>
  ret void
}

!air.kernel = !{!0}
!0 = !{ptr @probe, !1, !1}
!1 = !{}
