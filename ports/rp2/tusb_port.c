/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2019 Damien P. George
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

#include "tusb.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "pico/unique_id.h"

#ifndef MICROPY_HW_USB_VID
#define MICROPY_HW_USB_VID (0x2E8A) // Raspberry Pi
#endif
#ifndef MICROPY_HW_USB_PID
#define MICROPY_HW_USB_PID (0x0005) // RP2 MicroPython
#endif

#define USBD_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_HID_DESC_LEN)
#define USBD_MAX_POWER_MA (250)

enum 
{
  USBD_ITF_CDC = 0, USBD_ITF_CDC_DATA, // needs 2 interfaces
  USBD_ITF_HID,
  USBD_ITF_MAX
};

#define USBD_CDC_EP_CMD (0x81)
#define USBD_CDC_EP_OUT (0x02)
#define USBD_CDC_EP_IN (0x82)
#define USBD_CDC_CMD_MAX_SIZE (8)
#define USBD_CDC_IN_OUT_MAX_SIZE (64)

#define USBD_HID_EP_CMD (0x83)
#define USBD_HID_POLL_INTERVAL (10) // in ms

#define USBD_STR_0 (0x00)
#define USBD_STR_MANUF (0x01)
#define USBD_STR_PRODUCT (0x02)
#define USBD_STR_SERIAL (0x03)
#define USBD_STR_CDC (0x04)
#define USBD_STR_HID (0x05)


// Mouse Absolute Report Descriptor Template
#define TUD_HID_REPORT_DESC_MOUSE_ABS(...) \
  HID_USAGE_PAGE ( HID_USAGE_PAGE_DESKTOP      )                   ,\
  HID_USAGE      ( HID_USAGE_DESKTOP_MOUSE     )                   ,\
  HID_COLLECTION ( HID_COLLECTION_APPLICATION  )                   ,\
    /* Report ID if any */\
    __VA_ARGS__ \
    HID_USAGE      ( HID_USAGE_DESKTOP_POINTER )                   ,\
    HID_COLLECTION ( HID_COLLECTION_PHYSICAL   )                   ,\
      HID_USAGE_PAGE  ( HID_USAGE_PAGE_BUTTON  )                   ,\
        HID_USAGE_MIN   ( 1                                      ) ,\
        HID_USAGE_MAX   ( 5                                      ) ,\
        HID_LOGICAL_MIN ( 0                                      ) ,\
        HID_LOGICAL_MAX ( 1                                      ) ,\
        /* Left, Right, Middle, Backward, Forward buttons */ \
        HID_REPORT_COUNT( 5                                      ) ,\
        HID_REPORT_SIZE ( 1                                      ) ,\
        HID_INPUT       ( HID_DATA | HID_VARIABLE | HID_ABSOLUTE ) ,\
        /* 3 bit padding */ \
        HID_REPORT_COUNT( 1                                      ) ,\
        HID_REPORT_SIZE ( 3                                      ) ,\
        HID_INPUT       ( HID_CONSTANT                           ) ,\
      HID_USAGE_PAGE  ( HID_USAGE_PAGE_DESKTOP )                   ,\
        /* X, Y position [0, 32767] */ \
        HID_USAGE       ( HID_USAGE_DESKTOP_X                    ) ,\
        HID_USAGE       ( HID_USAGE_DESKTOP_Y                    ) ,\
        HID_LOGICAL_MIN_N ( 0x0000, 2                            ) ,\
        HID_LOGICAL_MAX_N ( 0x7fff, 2                            ) ,\
        HID_REPORT_COUNT( 2                                      ) ,\
        HID_REPORT_SIZE ( 16                                     ) ,\
        HID_INPUT       ( HID_DATA | HID_VARIABLE | HID_ABSOLUTE ) ,\
    HID_COLLECTION_END                                            , \
  HID_COLLECTION_END \


uint8_t const desc_hid_report[] =
{
  TUD_HID_REPORT_DESC_KEYBOARD (HID_REPORT_ID(REPORT_ID_KEYBOARD        )),
  TUD_HID_REPORT_DESC_MOUSE    (HID_REPORT_ID(REPORT_ID_MOUSE           )),
  TUD_HID_REPORT_DESC_MOUSE_ABS(HID_REPORT_ID(REPORT_ID_MOUSE_ABS       )),
  TUD_HID_REPORT_DESC_CONSUMER (HID_REPORT_ID(REPORT_ID_CONSUMER_CONTROL)),
  TUD_HID_REPORT_DESC_GAMEPAD  (HID_REPORT_ID(REPORT_ID_GAMEPAD         )),
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
  return desc_hid_report;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type, 
    uint8_t* buffer, uint16_t reqlen) {
  return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type, 
    uint8_t const* buffer, uint16_t bufsize) {

    // echo back anything we received from host
    // tud_hid_report(0, buffer, bufsize);
    return;
}

// Note: descriptors returned from callbacks must exist long enough for transfer to complete

static const tusb_desc_device_t usbd_desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = MICROPY_HW_USB_VID,
    .idProduct = MICROPY_HW_USB_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = USBD_STR_MANUF,
    .iProduct = USBD_STR_PRODUCT,
    .iSerialNumber = USBD_STR_SERIAL,
    .bNumConfigurations = 1,
};

static const uint8_t usbd_desc_cfg[USBD_DESC_LEN] = {
    TUD_CONFIG_DESCRIPTOR(1, USBD_ITF_MAX, USBD_STR_0, USBD_DESC_LEN,
        0, USBD_MAX_POWER_MA),

    TUD_CDC_DESCRIPTOR(USBD_ITF_CDC, USBD_STR_CDC, USBD_CDC_EP_CMD,
        USBD_CDC_CMD_MAX_SIZE, USBD_CDC_EP_OUT, USBD_CDC_EP_IN, USBD_CDC_IN_OUT_MAX_SIZE),

    // Interface number, string index, protocol, 
    // report descriptor len, EP In address, size & polling interval
    TUD_HID_DESCRIPTOR(USBD_ITF_HID, USBD_STR_HID, HID_ITF_PROTOCOL_NONE, 
        sizeof(desc_hid_report), USBD_HID_EP_CMD, CFG_TUD_HID_EP_BUFSIZE, USBD_HID_POLL_INTERVAL)
};

static const char *const usbd_desc_str[] = {
    [USBD_STR_MANUF] = "Robins Tools",
    [USBD_STR_PRODUCT] = "Pixel Pump",
    [USBD_STR_SERIAL] = NULL, // generated dynamically
    [USBD_STR_CDC] = "Board CDC",
    [USBD_STR_HID] = "Board HID",
};

const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&usbd_desc_device;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return usbd_desc_cfg;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    #define DESC_STR_MAX (20)
    static uint16_t desc_str[DESC_STR_MAX];

    uint8_t len;
    if (index == 0) {
        desc_str[1] = 0x0409; // supported language is English
        len = 1;
    } else {
        if (index >= sizeof(usbd_desc_str) / sizeof(usbd_desc_str[0])) {
            return NULL;
        }
        // check, if serial is requested
        if (index == USBD_STR_SERIAL) {
            pico_unique_board_id_t id;
            pico_get_unique_board_id(&id);
            // byte by byte conversion
            for (len = 0; len < 16; len += 2) {
                const char *hexdig = "0123456789abcdef";
                desc_str[1 + len] = hexdig[id.id[len >> 1] >> 4];
                desc_str[1 + len + 1] = hexdig[id.id[len >> 1] & 0x0f];
            }
        } else {
            const char *str = usbd_desc_str[index];
            for (len = 0; len < DESC_STR_MAX - 1 && str[len]; ++len) {
                desc_str[1 + len] = str[len];
            }
        }
    }

    // first byte is length (including header), second byte is string type
    desc_str[0] = (TUSB_DESC_STRING << 8) | (2 * len + 2);

    return desc_str;
}


const bool usbd_tud_hid_report(uint8_t report_id, void const* report, uint8_t len) {

    int retry = 100;
    while (!tud_hid_ready() && --retry >= 0) {
        if (retry == 0)
            mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("%s timeout (%d)"), 
                __FUNCTION__, report_id);
        mp_hal_delay_ms(1);
    }

    return tud_hid_report(report_id, report, len);
}
