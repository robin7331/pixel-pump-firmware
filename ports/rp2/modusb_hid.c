/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2020-2021 Damien P. George
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#include "py/runtime.h"
#include "tusb_config.h"

extern bool usbd_tud_hid_report(uint8_t report_id, void const* report, uint8_t len);

STATIC mp_obj_t usb_hid_report(mp_obj_t report_id_obj, mp_obj_t report_obj) {
    uint8_t report_id = mp_obj_get_int(report_id_obj);
    mp_buffer_info_t report;
    mp_get_buffer_raise(report_obj, &report, MP_BUFFER_READ);

    if (report_id < REPORT_ID_KEYBOARD || report_id > REPORT_ID_GAMEPAD) {
        mp_raise_msg_varg(&mp_type_ValueError, MP_ERROR_TEXT("report_id invalid (%d)"), report_id);
    }

    usbd_tud_hid_report(report_id, report.buf, report.len);
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_2(usb_hid_report_obj, usb_hid_report);

STATIC const mp_rom_map_elem_t usb_hid_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),                MP_ROM_QSTR(MP_QSTR_usb_hid) },

    { MP_ROM_QSTR(MP_QSTR_KEYBOARD),                MP_ROM_INT(REPORT_ID_KEYBOARD) },
    { MP_ROM_QSTR(MP_QSTR_MOUSE),                   MP_ROM_INT(REPORT_ID_MOUSE) },
    { MP_ROM_QSTR(MP_QSTR_MOUSE_ABS),               MP_ROM_INT(REPORT_ID_MOUSE_ABS) },
    { MP_ROM_QSTR(MP_QSTR_CONSUMER_CONTROL),        MP_ROM_INT(REPORT_ID_CONSUMER_CONTROL) },
    { MP_ROM_QSTR(MP_QSTR_GAMEPAD),                 MP_ROM_INT(REPORT_ID_GAMEPAD) },
    { MP_ROM_QSTR(MP_QSTR_report),                  MP_ROM_PTR(&usb_hid_report_obj) },
};
STATIC MP_DEFINE_CONST_DICT(usb_hid_module_globals, usb_hid_module_globals_table);

const mp_obj_module_t mp_module_usb_hid = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&usb_hid_module_globals,
};
