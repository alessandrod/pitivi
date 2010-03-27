
import gtk

from pitivi.ui.glade import GladeWindow

def get_color(c):
    return (
        c.props.color.red_float,
        c.props.color.green_float,
        c.props.color.blue_float,
        c.props.alpha / 65535.0)

class TitleEditDialog(GladeWindow):
    glade_file = "title_edit.glade"

    def __init__(self):
        GladeWindow.__init__(self)

        # Centre alignment is the default.
        self.widgets['radiobutton5'].props.active = True

        buffer = self.widgets['textview'].props.buffer
        buffer.set_text('Hello, World!')

        self.text = None
        self.font = None
        self.text_size = None
        self.bg_color = None
        self.fg_color = None

        # Hack: GladeWindow hides TitleEditDialog's run() with gtk.Dialog's;
        # undo that.
        del self.run

    def run(self):
        response = gtk.Dialog.run(self.window)

        buffer = self.widgets['textview'].props.buffer
        self.text = buffer.get_text(*buffer.get_bounds())

        font_name = self.widgets['fontbutton'].props.font_name
        self.font, size_str = font_name.rsplit(None, 1)
        self.text_size = int(size_str)

        self.bg_color = get_color(self.widgets['bgcolor_button'])
        self.fg_color = get_color(self.widgets['fgcolor_button'])

        for i, (x_alignment, y_alignment) in enumerate([
                (0.0, 0.0), (0.5, 0.0), (1.0, 0.0),
                (0.0, 0.5), (0.5, 0.5), (1.0, 0.5),
                (0.0, 1.0), (0.5, 1.0), (1.0, 1.0)]):
            if self.widgets['radiobutton%d' % (i + 1)].props.active:
                break

        self.x_alignment = x_alignment
        self.y_alignment = y_alignment
        return response

