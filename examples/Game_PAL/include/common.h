#ifndef COMMON_H
#define COMMON_H
/* Template only. Prove these primitive sizes against your original compiler.
 * Add 64/128-bit aliases only when the compiler and ABI establish their form.
 */
typedef signed char s8;
typedef unsigned char u8;
typedef short s16;
typedef unsigned short u16;
typedef int s32;
typedef unsigned int u32;
typedef float f32;
typedef double f64;
#define NULL ((void *)0)
#endif
