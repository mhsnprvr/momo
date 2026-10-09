local FONT = package.config:sub(1, 1) == "\\" and "Segoe UI" or "Helvetica Neue"
local CARD = "&H2A1D1B&"
local TRACK = "&H4A3633&"
local CORAL = "&H667AFF&"
local CORAL_LIGHT = "&HA6B3FF&"
local MUTED = "&HC1ADA9&"
local WHITE = "&HFFFFFF&"

local progress = {
    visible = false,
    target = 0,
    shown = 0,
    title = "",
    detail = "",
    started = mp.get_time(),
}
local badge = { visible = false, text = "", on = true, until_time = 0 }
local empty = { visible = false, hover = false, button = nil }

local card_overlay = mp.create_osd_overlay("ass-events")
local badge_overlay = mp.create_osd_overlay("ass-events")
local empty_overlay = mp.create_osd_overlay("ass-events")
local timer

local function clean(text)
    return (text or ""):gsub("[{}\\]", "")
end

local function rounded(x, y, w, h, r)
    r = math.min(r, w / 2, h / 2)
    local k = r * 0.552
    return string.format(
        "m %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f",
        x + r, y,
        x + w - r, y,
        x + w - r + k, y, x + w, y + r - k, x + w, y + r,
        x + w, y + h - r,
        x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h,
        x + r, y + h,
        x + r - k, y + h, x, y + h - r + k, x, y + h - r,
        x, y + r,
        x, y + r - k, x + r - k, y, x + r, y
    )
end

local function shape(path, color, alpha, extra)
    return string.format("{\\an7\\pos(0,0)\\bord0\\shad0%s\\1c%s\\1a&H%02X&\\p1}%s{\\p0}", extra or "", color, alpha, path)
end

local function outline(path, color, alpha, width)
    return string.format("{\\an7\\pos(0,0)\\bord%.1f\\shad0\\3c%s\\3a&H%02X&\\1a&HFF&\\p1}%s{\\p0}", width, color, alpha, path)
end

local function text(x, y, align, size, color, bold, value)
    return string.format(
        "{\\an%d\\pos(%.1f,%.1f)\\fn%s\\fs%.1f\\bord0\\shad0\\1c%s\\b%d}%s",
        align, x, y, FONT, size, color, bold and 1 or 0, clean(value)
    )
end

local function screen()
    local w, h = mp.get_osd_size()
    if not w or w <= 0 or not h or h <= 0 then
        w, h = 1280, 720
    end
    local s = math.max(0.55, math.min(w / 1280, h / 720))
    return w, h, s
end

local function render_card()
    if not progress.visible then
        card_overlay:remove()
        return
    end
    local w, h, s = screen()
    local cw, ch = 560 * s, 196 * s
    local cx, cy = (w - cw) / 2, (h - ch) / 2
    local pad = 40 * s
    local tx, ty, tw, th = cx + pad, cy + 104 * s, cw - pad * 2, 12 * s
    local events = {}

    events[#events + 1] = shape(string.format("m 0 0 l %d 0 %d %d 0 %d", w, w, h, h), "&H000000&", 0x70)
    events[#events + 1] = shape(rounded(cx, cy + 12 * s, cw, ch, 24 * s), "&H000000&", 0x60, string.format("\\blur%.1f", 22 * s))
    events[#events + 1] = shape(rounded(cx, cy, cw, ch, 24 * s), CARD, 0x0C)
    events[#events + 1] = shape(rounded(cx + pad, cy + 34 * s, 10 * s, 10 * s, 5 * s), CORAL, 0x00)
    events[#events + 1] = text(cx + pad + 22 * s, cy + 39 * s, 4, 15 * s, MUTED, true, "MOMO")
    events[#events + 1] = text(cx + pad, cy + 74 * s, 4, 30 * s, WHITE, true, progress.title)
    events[#events + 1] = shape(rounded(tx, ty, tw, th, th / 2), TRACK, 0x00)

    if progress.target >= 0 then
        local fill = math.max(th, tw * math.min(1, progress.shown))
        events[#events + 1] = shape(rounded(tx, ty, fill, th, th / 2), CORAL, 0x00)
        events[#events + 1] = shape(rounded(tx, ty, fill, th / 2, th / 4), CORAL_LIGHT, 0x90)
        events[#events + 1] = text(cx + cw - pad, cy + 74 * s, 6, 30 * s, CORAL, true, string.format("%d%%", math.floor(progress.shown * 100 + 0.5)))
    else
        local cycle = ((mp.get_time() - progress.started) % 1.6) / 1.6
        local seg = tw * 0.32
        local sx = tx - seg + (tw + seg) * cycle
        local clip = string.format("\\clip(%d,%d,%d,%d)", math.floor(tx), math.floor(ty), math.ceil(tx + tw), math.ceil(ty + th))
        events[#events + 1] = shape(rounded(sx, ty, seg, th, th / 2), CORAL, 0x00, clip)
    end

    events[#events + 1] = text(cx + pad, cy + 152 * s, 4, 19 * s, MUTED, false, progress.detail)
    card_overlay.res_x, card_overlay.res_y = w, h
    card_overlay.z = 10
    card_overlay.data = table.concat(events, "\n")
    card_overlay:update()
end

local function render_badge()
    if not badge.visible then
        badge_overlay:remove()
        return
    end
    local w, h, s = screen()
    local bw, bh = (66 + #badge.text * 10.5) * s, 46 * s
    local bx, by = (w - bw) / 2, 34 * s
    local events = {
        shape(rounded(bx, by + 6 * s, bw, bh, bh / 2), "&H000000&", 0x70, string.format("\\blur%.1f", 12 * s)),
        shape(rounded(bx, by, bw, bh, bh / 2), CARD, 0x10),
        shape(rounded(bx + 22 * s, by + bh / 2 - 5 * s, 10 * s, 10 * s, 5 * s), badge.on and CORAL or MUTED, 0x00),
        text(bx + 44 * s, by + bh / 2, 4, 20 * s, WHITE, true, badge.text),
    }
    badge_overlay.res_x, badge_overlay.res_y = w, h
    badge_overlay.z = 20
    badge_overlay.data = table.concat(events, "\n")
    badge_overlay:update()
end

local function render_empty()
    if not empty.visible then
        empty.button = nil
        empty_overlay:remove()
        return
    end
    local w, h, s = screen()
    local zw = math.min(640 * s, w - 48 * s)
    local zh = math.min(340 * s, h - 48 * s)
    local zx, zy = (w - zw) / 2, (h - zh) / 2
    local bw, bh = 240 * s, 54 * s
    local bx, by = (w - bw) / 2, zy + zh - bh - 44 * s
    local cx = w / 2
    local events = {
        shape(rounded(zx, zy, zw, zh, 28 * s), CARD, 0x30),
        outline(rounded(zx, zy, zw, zh, 28 * s), MUTED, 0x90, 2 * s),
        shape(rounded(cx - 26 * s, zy + 44 * s, 52 * s, 52 * s, 26 * s), CORAL, 0x00),
        shape(rounded(cx - 13 * s, zy + 68 * s, 26 * s, 4 * s, 2 * s), WHITE, 0x00),
        shape(rounded(cx - 2 * s, zy + 57 * s, 4 * s, 26 * s, 2 * s), WHITE, 0x00),
        text(cx, zy + 136 * s, 5, 30 * s, WHITE, true, "Drop videos here"),
        text(cx, zy + 174 * s, 5, 18 * s, MUTED, false, "MKV or MP4. One to watch, several to clean."),
        shape(rounded(bx, by, bw, bh, bh / 2), empty.hover and CORAL_LIGHT or CORAL, 0x00),
        text(cx, by + bh / 2, 5, 21 * s, WHITE, true, "Choose videos"),
    }
    empty.button = { x = bx, y = by, w = bw, h = bh }
    empty_overlay.res_x, empty_overlay.res_y = w, h
    empty_overlay.z = 5
    empty_overlay.data = table.concat(events, "\n")
    empty_overlay:update()
end

local function over_button()
    local mouse = mp.get_property_native("mouse-pos")
    local b = empty.button
    if not mouse or not b then
        return false
    end
    return mouse.x >= b.x and mouse.x <= b.x + b.w and mouse.y >= b.y and mouse.y <= b.y + b.h
end

local function pick()
    mp.commandv("script-message", "momo-pick")
end

local function click()
    if over_button() then
        pick()
    end
end

mp.observe_property("idle-active", "bool", function(_, idle)
    empty.visible = idle == true
    if empty.visible then
        mp.add_forced_key_binding("MBTN_LEFT", "momo-empty-click", click)
        mp.add_forced_key_binding("ENTER", "momo-empty-enter", pick)
    else
        mp.remove_key_binding("momo-empty-click")
        mp.remove_key_binding("momo-empty-enter")
    end
    render_empty()
end)

mp.observe_property("mouse-pos", "native", function()
    if not empty.visible then
        return
    end
    local hover = over_button()
    if hover ~= empty.hover then
        empty.hover = hover
        render_empty()
    end
end)

local function tick()
    local animating = false
    if progress.visible then
        if progress.target >= 0 then
            local gap = progress.target - progress.shown
            if math.abs(gap) > 0.001 then
                progress.shown = progress.shown + gap * 0.18
                animating = true
            else
                progress.shown = progress.target
            end
        else
            animating = true
        end
        render_card()
    end
    if badge.visible and mp.get_time() > badge.until_time then
        badge.visible = false
        render_badge()
    end
    if not animating and not badge.visible and timer then
        timer:kill()
        timer = nil
    end
end

local function ensure_timer()
    if not timer then
        timer = mp.add_periodic_timer(1 / 30, tick)
    end
end

mp.register_script_message("momo-progress", function(fraction, title, detail)
    local value = tonumber(fraction) or -1
    if not progress.visible then
        progress.shown = math.max(0, value)
        progress.started = mp.get_time()
    end
    progress.visible = true
    progress.target = value
    progress.title = title or ""
    progress.detail = detail or ""
    render_card()
    ensure_timer()
end)

mp.register_script_message("momo-progress-hide", function()
    progress.visible = false
    render_card()
end)

mp.register_script_message("momo-badge", function(value, on)
    badge.visible = true
    badge.text = value or ""
    badge.on = on ~= "off"
    badge.until_time = mp.get_time() + 1.8
    render_badge()
    ensure_timer()
end)

mp.observe_property("osd-dimensions", "native", function()
    render_card()
    render_badge()
    render_empty()
end)

local function toggle()
    local path = os.getenv("MOMO_TOGGLE")
    if not path or path == "" then
        return
    end
    local handle = io.open(path, "a")
    if handle then
        handle:write("x\n")
        handle:close()
    end
end

mp.add_key_binding("l", "momo-toggle", toggle)
mp.add_key_binding("L", "momo-toggle-shift", toggle)
