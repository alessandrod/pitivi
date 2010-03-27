
import gtk

from pitivi.ui.glade import GladeWindow

def get_color(c):
    return (
        c.props.color.red_float,
        c.props.color.green_float,
        c.props.color.blue_float,
        c.props.alpha / 65535.0)

def set_color(c, t):
    c.props.color.red_float = t[0]
    c.props.color.red_float = t[1]
    c.props.color.red_float = t[2]
    c.props.alpha = int(t[3] * 65535.0)

alignments = [
        (0.0, 0.0), (0.5, 0.0), (1.0, 0.0),
        (0.0, 0.5), (0.5, 0.5), (1.0, 0.5),
        (0.0, 1.0), (0.5, 1.0), (1.0, 1.0)]

class TitleEditDialog(GladeWindow):
    glade_file = "title_edit.glade"

    def __init__(self, **kw):
        GladeWindow.__init__(self)

        self.text = kw.get('text', 'Hello, World!')
        self.font = kw.get('font', 'Sans')
        self.text_size = kw.get('text_size', 64)
        self.bg_color = kw.get('bg_color', (0, 0, 0, 1))
        self.fg_color = kw.get('fg_color', (1, 1, 1, 1))
        # Centre alignment is the default.
        self.x_alignment = 0.5
        self.y_alignment = 0.5

        # Hack: GladeWindow hides TitleEditDialog's run() with gtk.Dialog's;
        # undo that.
        del self.run

    def set(self, **kw):
        self.__dict__.update(kw)

    def _copy_to_dialog(self):
        buffer = self.widgets['textview'].props.buffer
        buffer.set_text(self.text)

        set_color(self.widgets['bgcolor_button'], self.bg_color)
        set_color(self.widgets['fgcolor_button'], self.fg_color)

        self.widgets['fontbutton'].props.font_name = \
            '%s %d' % (self.font, self.text_size)

        for i, (x_alignment, y_alignment) in enumerate(alignments):
            if (self.x_alignment == x_alignment and
                self.y_alignment == y_alignment):
                self.widgets['radiobutton%d' % (i + 1)].props.active = True

    def _copy_from_dialog(self):
        buffer = self.widgets['textview'].props.buffer
        self.text = buffer.get_text(*buffer.get_bounds())

        font_name = self.widgets['fontbutton'].props.font_name
        self.font, size_str = font_name.rsplit(None, 1)
        self.text_size = int(size_str)

        self.bg_color = get_color(self.widgets['bgcolor_button'])
        self.fg_color = get_color(self.widgets['fgcolor_button'])

        for i, (x_alignment, y_alignment) in enumerate(alignments):
            if self.widgets['radiobutton%d' % (i + 1)].props.active:
                break

        self.x_alignment = x_alignment
        self.y_alignment = y_alignment

    def run(self):
        self._copy_to_dialog()
        response = gtk.Dialog.run(self.window)
        self._copy_from_dialog()
        return response

