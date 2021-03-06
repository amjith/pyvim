"""
The actual layout for the renderer.
"""
from __future__ import unicode_literals
from prompt_toolkit.filters import HasFocus, HasSearch, Condition, HasArg, Always
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.layout import HSplit, VSplit, FloatContainer, Float
from prompt_toolkit.layout.containers import Window, ConditionalContainer, ScrollOffsets
from prompt_toolkit.layout.controls import BufferControl, FillControl
from prompt_toolkit.layout.controls import TokenListControl
from prompt_toolkit.layout.margins import ConditionalMargin, NumberredMargin
from prompt_toolkit.layout.dimension import LayoutDimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import Processor, HighlightSearchProcessor, HighlightSelectionProcessor, HighlightMatchingBracketProcessor, ConditionalProcessor, BeforeInput, ShowTrailingWhiteSpaceProcessor, Transformation
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.layout.toolbars import TokenListToolbar, SystemToolbar, SearchToolbar, ValidationToolbar, CompletionsToolbar
from prompt_toolkit.selection import SelectionType

from pygments.token import Token

from .commands.lexer import create_command_lexer
from .enums import COMMAND_BUFFER
from .lexer import DocumentLexer
from .welcome_message import WELCOME_MESSAGE_TOKENS, WELCOME_MESSAGE_HEIGHT, WELCOME_MESSAGE_WIDTH

import pyvim.window_arrangement as window_arrangement

import re

__all__ = (
    'EditorLayout',
)


class TabsControl(TokenListControl):
    """
    Displays the tabs at the top of the screen, when there is more than one
    open tab.
    """
    def __init__(self, editor):
        def location_for_tab(tab):
            return tab.active_window.editor_buffer.get_display_name(short=True)

        def get_tokens(cli):
            selected_tab_index = editor.window_arrangement.active_tab_index

            result = []
            append = result.append

            for i, tab in enumerate(editor.window_arrangement.tab_pages):
                caption = location_for_tab(tab)
                if tab.has_unsaved_changes:
                    caption = ' + ' + caption

                if i == selected_tab_index:
                    append((Token.TabBar.Tab.Active, ' %s ' % caption))
                else:
                    append((Token.TabBar.Tab, ' %s ' % caption))
                append((Token.TabBar, ' '))

            return result

        super(TabsControl, self).__init__(get_tokens, Char(token=Token.TabBar))


class TabsToolbar(ConditionalContainer):
    def __init__(self, editor):
        super(TabsToolbar, self).__init__(
            Window(TabsControl(editor), height=LayoutDimension.exact(1)),
            filter=Condition(lambda cli: len(editor.window_arrangement.tab_pages) > 1))


class CommandLine(ConditionalContainer):
    """
    The editor command line. (For at the bottom of the screen.)
    """
    def __init__(self):
        super(CommandLine, self).__init__(
            Window(
                BufferControl(
                    buffer_name=COMMAND_BUFFER,
                    input_processors=[BeforeInput.static(':')],
                    lexer=create_command_lexer()),
                height=LayoutDimension.exact(1)),
            filter=HasFocus(COMMAND_BUFFER))


class WelcomeMessageWindow(ConditionalContainer):
    """
    Welcome message pop-up, which is shown during start-up when no other files
    were opened.
    """
    def __init__(self, editor):
        once_hidden = [False]  # Nonlocal

        def condition(cli):
            # Get editor buffers
            buffers = editor.window_arrangement.editor_buffers

            # Only show when there is only one empty buffer, but once the
            # welcome message has been hidden, don't show it again.
            result = (len(buffers) == 1 and buffers[0].buffer.text == '' and
                      buffers[0].location is None and not once_hidden[0])
            if not result:
                once_hidden[0] = True
            return result

        super(WelcomeMessageWindow, self).__init__(
            Window(TokenListControl(lambda cli: WELCOME_MESSAGE_TOKENS)),
            filter=Condition(condition))


def _bufferlist_overlay_visible_condition(cli):
    """
    True when the buffer list overlay should be displayed.
    (This is when someone starts typing ':b' or ':buffer' in the command line.)
    """
    text = cli.buffers[COMMAND_BUFFER].text.lstrip()
    return cli.current_buffer_name == COMMAND_BUFFER and (
            any(text.startswith(p) for p in ['b ', 'b! ', 'buffer', 'buffer!']))

bufferlist_overlay_visible_filter = Condition(_bufferlist_overlay_visible_condition)


class BufferListOverlay(ConditionalContainer):
    """
    Floating window that shows the list of buffers when we are typing ':b'
    inside the vim command line.
    """
    def __init__(self, editor):
        token = Token.BufferList

        def highlight_location(location, search_string, default_token):
            """
            Return a tokenlist with the `search_string` highlighted.
            """
            result = [(default_token, c) for c in location]

            # Replace token of matching positions.
            for m in re.finditer(re.escape(search_string), location):
                for i in range(m.start(), m.end()):
                    result[i] = (token.SearchMatch, result[i][1])
            return result

        def get_tokens(cli):
            wa = editor.window_arrangement
            buffer_infos = wa.list_open_buffers()

            # Filter infos according to typed text.
            input_params = cli.buffers[COMMAND_BUFFER].text.lstrip().split(None, 1)
            search_string = input_params[1] if len(input_params) > 1 else ''

            if search_string:
                def matches(info):
                    """
                    True when we should show this entry.
                    """
                    # When the input appears in the location.
                    if input_params[1] in (info.editor_buffer.location or ''):
                        return True

                    # When the input matches this buffer his index number.
                    if input_params[1] in str(info.index):
                        return True

                    # When this entry is part of the current completions list.
                    b = cli.buffers[COMMAND_BUFFER]

                    if b.complete_state and any(info.editor_buffer.location in c.display
                                                for c in b.complete_state.current_completions
                                                if info.editor_buffer.location is not None):
                        return True

                    return False

                buffer_infos = [info for info in buffer_infos if matches(info)]

            # Render output.
            if len(buffer_infos) == 0:
                return [(token, ' No match found. ')]
            else:
                result = []

                # Create title.
                result.append((token, '  '))
                result.append((token.Title, 'Open buffers\n'))

                # Get length of longest location
                max_location_len = max(len(info.editor_buffer.get_display_name()) for info in buffer_infos)

                # Show info for each buffer.
                for info in buffer_infos:
                    eb = info.editor_buffer
                    char = '%' if info.is_active else ' '
                    char2 = 'a' if info.is_visible else ' '
                    char3 = ' + ' if info.editor_buffer.has_unsaved_changes else '   '
                    t = token.Active if info.is_active else token

                    result.extend([
                        (token, ' '),
                        (t, '%3i ' % info.index),
                        (t, '%s' % char),
                        (t, '%s ' % char2),
                        (t, '%s ' % char3),
                    ])
                    result.extend(highlight_location(eb.get_display_name(), search_string, t))
                    result.extend([
                        (t, ' ' * (max_location_len - len(eb.get_display_name()))),
                        (t.Lineno, '  line %i' % (eb.buffer.document.cursor_position_row + 1)),
                        (t, ' \n')
                    ])
                return result

        super(BufferListOverlay, self).__init__(
            Window(TokenListControl(get_tokens, default_char=Char(token=token))),
            filter=bufferlist_overlay_visible_filter)


class MessageToolbarBar(TokenListToolbar):
    """
    Pop-up (at the bottom) for showing error/status messages.
    """
    def __init__(self, editor):
        def get_tokens(cli):
            if editor.message:
                return [(Token.Message, editor.message)]
            else:
                return []

        super(MessageToolbarBar, self).__init__(
                get_tokens,
                filter=Condition(lambda cli: editor.message is not None))


class ReportMessageToolbar(TokenListToolbar):
    """
    Toolbar that shows the messages, given by the reporter.
    (It shows the error message, related to the current line.)
    """
    def __init__(self, editor):
        def get_tokens(cli):
            eb = editor.window_arrangement.active_editor_buffer

            lineno = eb.buffer.document.cursor_position_row
            errors = eb.report_errors

            for e in errors:
                if e.lineno == lineno:
                    return e.message_token_list

            return []

        super(ReportMessageToolbar, self).__init__(
                get_tokens,
                filter=~HasFocus(COMMAND_BUFFER) & ~HasSearch() & ~HasFocus('system'))


class WindowStatusBar(TokenListToolbar):
    """
    The status bar, which is shown below each window in a tab page.
    """
    def __init__(self, editor, editor_buffer, manager):
        def get_tokens(cli):
            insert_mode = manager.vi_state.input_mode == InputMode.INSERT
            replace_mode = manager.vi_state.input_mode == InputMode.REPLACE
            sel = cli.buffers[editor_buffer.buffer_name].selection_state
            visual_line = sel is not None and sel.type == SelectionType.LINES
            visual_char = sel is not None and sel.type == SelectionType.CHARACTERS

            def mode():
                if cli.current_buffer_name == editor_buffer.buffer_name:
                    if insert_mode:
                        if editor.paste_mode:
                            return ' -- INSERT (paste)--'
                        else:
                            return ' -- INSERT --'
                    elif replace_mode:
                        return ' -- REPLACE --'
                    elif visual_line:
                        return ' -- VISUAL LINE --'
                    elif visual_char:
                        return ' -- VISUAL --'
                return '                     '

            return [
                (Token.Toolbar, ' '),
                (Token.Toolbar, editor_buffer.location or ''),
                (Token.Toolbar, ' [New File]' if editor_buffer.is_new else ''),
                (Token.Toolbar, '*' if editor_buffer.has_unsaved_changes else ''),
                (Token.Toolbar, ' '),
                (Token.Toolbar, mode()),
            ]
        super(WindowStatusBar, self).__init__(get_tokens, default_char=Char(' ', Token.Toolbar))


class WindowStatusBarRuler(ConditionalContainer):
    """
    The right side of the Vim toolbar, showing the location of the cursor in
    the file, and the vectical scroll percentage.
    """
    def __init__(self, editor, buffer_window, buffer_name):
        def get_scroll_text():
            info = buffer_window.render_info

            if info:
                if info.full_height_visible:
                    return 'All'
                elif info.top_visible:
                    return 'Top'
                elif info.bottom_visible:
                    return 'Bot'
                else:
                    percentage = info.vertical_scroll_percentage
                    return '%2i%%' % percentage

            return ''

        def get_tokens(cli):
            main_document = cli.buffers[buffer_name].document

            return [
                (Token.Toolbar.CursorPosition, '(%i,%i)' % (main_document.cursor_position_row + 1,
                                                            main_document.cursor_position_col + 1)),
                (Token.Toolbar, ' - '),
                (Token.Toolbar.Percentage, get_scroll_text()),
                (Token.Toolbar, ' '),
            ]

        super(WindowStatusBarRuler, self).__init__(
            Window(
                TokenListControl(get_tokens, default_char=Char(' ', Token.Toolbar), align_right=True),
                height=LayoutDimension.exact(1),
                width=LayoutDimension.exact(15)),
            filter=Condition(lambda cli: editor.show_ruler))


class SimpleArgToolbar(ConditionalContainer):
    """
    Simple control showing the Vi repeat arg.
    """
    def __init__(self):
        def get_tokens(cli):
            if cli.input_processor.arg is not None:
                return [(Token.Arg, ' %i ' % cli.input_processor.arg)]
            else:
                return []

        super(SimpleArgToolbar, self).__init__(
            Window(TokenListControl(get_tokens, align_right=True)),
            filter=HasArg()),


class PyvimScrollOffsets(ScrollOffsets):
    def __init__(self, editor):
        self.editor = editor
        self.left = 0
        self.right = 0

    @property
    def top(self):
        return self.editor.scroll_offset

    @property
    def bottom(self):
        return self.editor.scroll_offset


class EditorLayout(object):
    """
    The main layout class.
    """
    def __init__(self, editor, manager, window_arrangement):
        self.editor = editor  # Back reference to editor.
        self.manager = manager
        self.window_arrangement = window_arrangement

        # Mapping from (`window_arrangement.Window`, `EditorBuffer`) to a frame
        # (Layout instance).
        # We keep this as a cache in order to easily reuse the same frames when
        # the layout is updated. (We don't want to create new frames on every
        # update call, because that way, we would loose some state, like the
        # vertical scroll offset.)
        self._frames = {}

        self._fc = FloatContainer(
            content=VSplit([
                Window(BufferControl())  # Dummy window
            ]),
            floats=[
                Float(xcursor=True, ycursor=True,
                      content=CompletionsMenu(max_height=12,
                                              scroll_offset=2,
                                              extra_filter=~HasFocus(COMMAND_BUFFER))),
                Float(content=BufferListOverlay(editor), bottom=1, left=0),
                Float(bottom=1, left=0, right=0, height=1,
                      content=CompletionsToolbar(
                          extra_filter=HasFocus(COMMAND_BUFFER) &
                                       ~bufferlist_overlay_visible_filter &
                                       Condition(lambda cli: editor.show_wildmenu))),
                Float(bottom=1, left=0, right=0, height=1,
                      content=ValidationToolbar()),
                Float(bottom=1, left=0, right=0, height=1,
                      content=MessageToolbarBar(editor)),
                Float(content=WelcomeMessageWindow(editor),
                      height=WELCOME_MESSAGE_HEIGHT,
                      width=WELCOME_MESSAGE_WIDTH),
            ]
        )

        self.layout = FloatContainer(
            content=HSplit([
                TabsToolbar(editor),
                self._fc,
                CommandLine(),
                ReportMessageToolbar(editor),
                SystemToolbar(),
                SearchToolbar(vi_mode=True),
            ]),
            floats=[
                Float(right=0, height=1, bottom=0, width=5,
                      content=SimpleArgToolbar()),
            ]
        )

    def update(self):
        """
        Update layout to match the layout as described in the
        WindowArrangement.
        """
        # Start with an empty frames list everytime, to avoid memory leaks.
        existing_frames = self._frames
        self._frames = {}

        def create_layout_from_node(node):
            if isinstance(node, window_arrangement.Window):
                # Create frame for Window, or reuse it, if we had one already.
                key = (node, node.editor_buffer)
                frame = existing_frames.get(key)
                if frame is None:
                    frame = self._create_window_frame(node.editor_buffer)
                self._frames[key] = frame
                return frame

            elif isinstance(node, window_arrangement.VSplit):
                children = []
                for n in node:
                    children.append(create_layout_from_node(n))
                    children.append(Window(width=LayoutDimension.exact(1),
                                           content=FillControl('\u2502', token=Token.FrameBorder)))
                children.pop()
                return VSplit(children)

            if isinstance(node, window_arrangement.HSplit):
                return HSplit([create_layout_from_node(n) for n in node])

        layout = create_layout_from_node(self.window_arrangement.active_tab.root)
        self._fc.content = layout

    def _create_window_frame(self, editor_buffer):
        """
        Create a Window for the buffer, with underneat a status bar.
        """
        window = Window(self._create_buffer_control(editor_buffer),
                        allow_scroll_beyond_bottom=Always(),
                        scroll_offsets=PyvimScrollOffsets(self.editor),
                        left_margins=[ConditionalMargin(
                                margin=NumberredMargin(
                                    buffer_name=editor_buffer.buffer_name,
                                    relative=Condition(lambda cli: self.editor.relative_number)),
                                filter=Condition(lambda cli: self.editor.show_line_numbers))])


        return HSplit([
            window,
            VSplit([
                WindowStatusBar(self.editor, editor_buffer, self.manager),
                WindowStatusBarRuler(self.editor, window, editor_buffer.buffer_name),
            ]),
        ])

    def _create_buffer_control(self, editor_buffer):
        """
        Create a new BufferControl for a given location.
        """
        buffer_name = editor_buffer.buffer_name

        @Condition
        def preview_search(cli):
            return self.editor.incsearch

        @Condition
        def wrap_lines(cli):
            return self.editor.wrap_lines

        input_processors = [
            # Highlighting of the search.
            ConditionalProcessor(
                HighlightSearchProcessor(preview_search=preview_search),
                Condition(lambda cli: self.editor.highlight_search)),

            # Processor for visualising spaces. (should come before the
            # selection processor, otherwise, we won't see these spaces
            # selected.)
            ConditionalProcessor(
                ShowTrailingWhiteSpaceProcessor(),
                Condition(lambda cli: self.editor.display_unprintable_characters)),

            # Highlight selection.
            HighlightSelectionProcessor(),

            # Highlight matching parentheses.
            HighlightMatchingBracketProcessor(),

            # Reporting of errors, for Pyflakes.
            ReportingProcessor(editor_buffer),

            # Replace tabs by spaces.
            TabsProcessor(self.editor)]

        return BufferControl(lexer=DocumentLexer(editor_buffer),
                             input_processors=input_processors,
                             buffer_name=buffer_name,
                             preview_search=preview_search,
                             wrap_lines=wrap_lines)


class ReportingProcessor(Processor):
    """
    Highlight all pyflakes errors on the input.
    """
    def __init__(self, editor_buffer):
        self.editor_buffer = editor_buffer

    def apply_transformation(self, cli, document, tokens):
        if self.editor_buffer.report_errors:
            for error in self.editor_buffer.report_errors:
                for i in range(error.start_index, error.end_index):
                    if i < len(tokens):
                        tokens[i] = (Token.FlakesError, tokens[i][1])

        return Transformation(document, tokens)

    def invalidation_hash(self, cli, document):
        return (self.editor_buffer.report_errors, )



class TabsProcessor(Processor):
    """
    Render tabs as spaces or make them visible.
    """
    def __init__(self, editor):
        self.editor = editor

    def apply_transformation(self, cli, document, tokens):
        tabstop = self.editor.tabstop

        # Create separator for tabs.
        if self.editor.display_unprintable_characters:
            dots =  '\u2508'
            separator = dots * tabstop
            token = Token.Tab
        else:
            separator = ' ' * tabstop
            token = None  # Don't replace the token.

        # Remember the positions where we replace the tab.
        positions = set()

        # Replace tab by separator.
        for i, value in enumerate(tokens):
            if value[1] == '\t':
                positions.add(i-1)
                tokens[i] = (token or tokens[i][0], separator)

        def source_to_display(from_position):
            """ Maps original cursor position to the new one. """
            count = len(list(p for p in positions if p < from_position -1))
            return from_position + count * (tabstop - 1)

        def display_to_source(display_pos):
            return display_pos  # XXX

        return Transformation(
                document,
                tokens,
                source_to_display=source_to_display,
                display_to_source=display_to_source)

    def invalidation_hash(self, cli, document):
        return (self.editor.tabstop, )
