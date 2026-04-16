-- Pandoc Lua filter for Obsidian-specific markdown features

-- Map callout types to display labels and optional prefix symbols
local callout_labels = {
  note = "NOTE",
  tip = "TIP",
  important = "IMPORTANT",
  warning = "WARNING",
  caution = "CAUTION",
  abstract = "ABSTRACT",
  summary = "SUMMARY",
  tldr = "TL;DR",
  info = "INFO",
  todo = "TODO",
  success = "SUCCESS",
  check = "CHECK",
  done = "DONE",
  question = "QUESTION",
  help = "HELP",
  faq = "FAQ",
  failure = "FAILURE",
  fail = "FAIL",
  missing = "MISSING",
  danger = "DANGER",
  error = "ERROR",
  bug = "BUG",
  example = "EXAMPLE",
  quote = "QUOTE",
  cite = "CITE",
}

-- Convert callouts (> [!TYPE] content) to styled blockquotes
-- Handles: > [!TYPE], > [!TYPE] title, > [!TYPE]+ (foldable open), > [!TYPE]- (foldable closed)
function BlockQuote(el)
  if #el.content == 0 then
    return el
  end

  local first_block = el.content[1]
  if first_block.t ~= "Para" and first_block.t ~= "Plain" then
    return el
  end

  local first_inline = first_block.content[1]
  if first_inline == nil or first_inline.t ~= "Str" then
    return el
  end

  -- Match [!TYPE] or [!TYPE]+ or [!TYPE]- pattern
  local callout_type = first_inline.text:match("^%[!(%w+)%][%+%-]?$")
  if callout_type == nil then
    return el
  end

  local label_text = callout_labels[callout_type:lower()] or callout_type:upper()

  -- Collect title inlines (everything after [!TYPE] on the first line)
  local title_inlines = pandoc.List()
  local skip_space = true
  for i = 2, #first_block.content do
    local inline = first_block.content[i]
    if skip_space and (inline.t == "Space" or inline.t == "SoftBreak") then
      skip_space = false
    else
      skip_space = false
      title_inlines:insert(inline)
    end
  end

  -- Build the label: "TYPE" or "TYPE: custom title"
  local label_inlines = pandoc.List()
  label_inlines:insert(pandoc.Strong({pandoc.Str(label_text)}))
  if #title_inlines > 0 then
    label_inlines:insert(pandoc.Str(": "))
    label_inlines:extend(title_inlines)
  end

  -- Rebuild the blockquote: label paragraph + remaining content blocks
  local new_blocks = pandoc.Blocks({pandoc.Para(label_inlines)})
  for i = 2, #el.content do
    new_blocks:insert(el.content[i])
  end

  el.content = new_blocks
  return el
end

-- Convert ==highlight== to emphasized text
-- Handles highlights that may span across Str elements within a single inline
function Str(el)
  local text = el.text
  if not text:find("==") then
    return el
  end

  local result = pandoc.List()
  local pos = 1

  while pos <= #text do
    local start_pos = text:find("==", pos, true)
    if start_pos == nil then
      result:insert(pandoc.Str(text:sub(pos)))
      break
    end

    -- Add text before the marker
    if start_pos > pos then
      result:insert(pandoc.Str(text:sub(pos, start_pos - 1)))
    end

    -- Find closing ==
    local end_pos = text:find("==", start_pos + 2, true)
    if end_pos == nil then
      -- No closing marker — output the == literally
      result:insert(pandoc.Str(text:sub(start_pos)))
      break
    end

    -- Create emphasized text for the highlight
    local highlighted = text:sub(start_pos + 2, end_pos - 1)
    if #highlighted > 0 then
      result:insert(pandoc.Emph({pandoc.Str(highlighted)}))
    end
    pos = end_pos + 2
  end

  if #result == 1 then
    return result[1]
  elseif #result > 1 then
    return result
  end

  return el
end
