-- Checks the relative links of the Markdown and HTML files listed in a file, one site path per line, run from the
-- composed site (compose.py). A link resolves when it points at a file of the site outside hidden paths: X.md, the page
-- the site renders from it (X.md.html), X.html when only X.md exists (the site redirects it), or any other file. A `..`
-- above the site root stays at the root, as browsers resolve it. External URLs and in-page anchors are not checked, and
-- neither are fragments.
--
-- Usage: pandoc lua links.lua LIST

local list = arg[1]
if not list then
  io.stderr:write('usage: pandoc lua links.lua LIST\n')
  os.exit(2)
end

local function read(path)
  local f = assert(io.open(path, 'rb'))
  local text = f:read('a')
  f:close()
  return text
end

local function is_dir(path)
  return (pcall(pandoc.system.list_directory, path))
end

local function is_file(path)
  if path == '' or is_dir(path) then
    return false
  end
  local f = io.open(path, 'rb')
  if f then
    f:close()
    return true
  end
  return false
end

-- Attribute values that are URLs, found in HTML files and in raw HTML inside Markdown.
local url_attributes = { href = true, src = true, ['xlink:href'] = true }

local function html_links(text, out)
  for name, _, value in text:gmatch('([%w:%-]+)%s*=%s*(["\'])(.-)%2') do
    if url_attributes[name:lower()] then
      table.insert(out, value)
    end
  end
end

-- markdown_links returns the links of a Markdown text, or nil and why it cannot be parsed.
local function markdown_links(text)
  local ok, doc = pcall(pandoc.read, text, 'gfm')
  if not ok then
    return nil, tostring(doc)
  end
  local out = {}
  local function raw(el)
    if el.format == 'html' then
      html_links(el.text, out)
    end
  end
  doc:walk({
    Link = function(el) table.insert(out, el.target) end,
    Image = function(el) table.insert(out, el.src) end,
    RawInline = raw,
    RawBlock = raw,
  })
  return out
end

-- resolve returns the repository path a link from file points at, or nil and why it cannot.
local function resolve(file, link)
  local path = link:gsub('[?#].*$', '')
  path = path:gsub('%%(%x%x)', function(hex) return string.char(tonumber(hex, 16)) end)
  local segments = {}
  local start = path
  if path:sub(1, 1) == '/' then
    start = path:sub(2)
  else
    for segment in pandoc.path.directory(file):gmatch('[^/]+') do
      if segment ~= '.' then
        table.insert(segments, segment)
      end
    end
  end
  for segment in start:gmatch('[^/]+') do
    if segment == '..' then
      table.remove(segments) -- a no-op at the root
    elseif segment ~= '.' then
      table.insert(segments, segment)
    end
  end
  for _, segment in ipairs(segments) do
    if segment:sub(1, 1) == '.' then
      return nil, 'points into a hidden path, which is not published'
    end
  end
  return table.concat(segments, '/')
end

local function check(file, link)
  if link == '' or link:sub(1, 1) == '#' or link:match('^%a[%w+.-]*:') or link:sub(1, 2) == '//' then
    return nil -- an in-page anchor or an external URL
  end
  local path, why = resolve(file, link)
  if not path then
    return why
  end
  if is_file(path) then
    return nil
  end
  local page = path:match('^(.+%.md)%.html$')
  if page and is_file(page) then
    return nil
  end
  local legacy = path:match('^(.+)%.html$')
  if legacy and not page and is_file(legacy .. '.md') then
    return nil
  end
  if path == '' or is_dir(path) then
    return 'is a directory; the site has no index pages'
  end
  return 'does not exist'
end

local failures, checked = 0, 0
for file in read(list):gmatch('[^\n]+') do
  local links, why
  if file:match('%.md$') then
    links, why = markdown_links(read(file))
    if not links then
      failures = failures + 1
      io.stderr:write(('error: %s: cannot be parsed: %s\n'):format(file, why))
    end
  elseif file:match('%.html?$') then
    links = {}
    html_links(read(file), links)
  end
  for _, link in ipairs(links or {}) do
    checked = checked + 1
    local problem = check(file, link)
    if problem then
      failures = failures + 1
      io.stderr:write(('error: %s: link %s %s\n'):format(file, link, problem))
    end
  end
end
print(('Checked %d link(s): %d problem(s)'):format(checked, failures))
if failures > 0 then
  os.exit(1)
end
