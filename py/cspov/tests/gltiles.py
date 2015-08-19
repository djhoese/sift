#!/usr/bin/env python
# encoding: utf-8
"""
All coordinates are (y,x) pairs

We assume a mercator projection using a WGS84 geoid
Scene coordinate are meters along the 0 parallel
y range +/- 6356752.3142 * 2 * pi => 39,940,652.742
x range +/- 6378137.0 * 2 * pi => 40,075,016.6856

"eqm" = equator-meter, we're using mercator projection as a baseline with

References
https://bitbucket.org/rndblnch/opengl-programmable/raw/29d0c699c82a2ca961014e7eb5e6cd3a87fe5883/05-shader.py

Layers
    can hold more than one level of detail (LayerRepresentation), only 1-2 of which are activated at any given time
    can paint to multiple GLWidgets
    render draw-lists and textures that draw a subset of the overall layer data, with a suitable level of detail


Rendering loop
    For each layer
        call Layer's paint, giving it information about the extents and sampling so it can pick best of the ready representations
        note whether the layer returned False and is requesting a re-render when the dust has settled => add to dirty layer list

Idle loop
    For each dirty layer
        Have layer render new draw list
        If layer has problems rendering, call a purge on all layers and then re-render


"""

import sys
from collections import namedtuple
from OpenGL.GL import *
from PyQt4.QtGui import *
from PyQt4.QtCore import *
from PyQt4.QtOpenGL import *
from PIL import Image
import numpy as np


DEFAULT_TILE_HEIGHT = 256   # 180°
DEFAULT_TILE_WIDTH = 512   # 360°


# mercator
MAX_SCENE_Y = 39940660.0
MAX_SCENE_X = 40075020.0

box = namedtuple('box', ('b', 'l', 't', 'r'))  # bottom, left, top, right
rez = namedtuple('rez', ('dy', 'dx'))
pnt = namedtuple('pnt', ('y', 'x'))
geo = namedtuple('geo', ('n', 'e'))  # lat N, lon E

# eqm coordinates describing a view
view_geometry = namedtuple('view_geometry', ('b', 'l', 't', 'r', 'dy', 'dx'))


class CoordSystem(object):
    """
    converts (y,x) pixel coordinates to geodetic coordinates in meters or degrees (lat/lon)
    """
    UNKNOWN = 0
    DATA_PIXELS = 1     # storage pixels for a given layer
    SCREEN_PIXELS = 2   # OpenGL pixels
    LATLON_DEGREES = 4  # lat/lon


class Layer(object):
    """
    A Layer
    - has one or more representations available to immediately draw
    - may want to schedule the rendering of other representations during idle time, to get ideal view
    - may have a backing science representation which is pure science data instead of pixel values or RGBA maps
    - typically will cache a "coarsest" single-tile representation for zoom-out events (preferred for fast=True paint calls)
    - can have probes attached which operate primarily on the science representation
    """
    def paint(self, geom, fast=False):
        """
        draw the most appropriate representation for this layer
        if a better representation could be rendered for later draws, return False and render() will be queued for later idle time
        fast flag requests that low-cost rendering be used
        """
        return True

    def render(self, geom, *more_geom):
        """
        cache a rendering (typically a draw-list with textures) that best handles the extents and sampling requested
        if more than one view is active, more geometry may be provided for other views
        return False if resources were too limited and a purge is needed among the layer stack
        """
        return True

    def purge(self, geom, *more_geom):
        """
        release any cached representations that we haven't used lately, leaving at most 1
        return True if any GL resources were released
        """
        return False

    def probe_point_xy(self, x, y):
        """
        return a value array for the requested point as specified in mercator-meters
        """
        raise NotImplementedError()

    def probe_point_geo(self, lat, lon):
        """
        """
        raise NotImplementedError()

    def probe_shape(self, geo_shape):
        """
        given a shapely description of an area, return a masked array of data
        """
        raise NotImplementedError()


class MercatorTileCalc(object):
    """
    common calculations for mercator tile groups in an array or file
    tiles are identified by (iy,ix) zero-based indicators
    """
    OVERSAMPLED=1
    UNDERSAMPLED=-1
    WELLSAMPLED=0

    name = None
    pixel_shape = None
    pixel_rez = None
    zero_point = None
    tile_shape = None
    # derived
    extents_box = None  # word coordinates that this image and its tiles corresponds to
    tiles_avail = None  # (ny,nx) available tile count for this image

    def __init__(self, name, pixel_shape, zero_point, pixel_rez, tile_shape=(DEFAULT_TILE_HEIGHT, DEFAULT_TILE_WIDTH)):
        """
        name: the 'name' of the tile, typically the path of the file it represents
        pixel_shape: (h:int,w:int) in pixels
        zero_point: (y:float,x:float) in pixels that represents world coords 0N,0E eqm, even if outside the image and even if fractional
        pixel_rez: (dy:float,dx:float) in world coords per pixel ascending from corner [0,0]
        tile_shape: the pixel dimensions (h:int, w:int) of the GPU tiling we want to use

        Tiling is aligned to pixels, not world
        World coordinates are eqm such that 0,0 matches 0°N 0°E, going north/south +-90° and west/east +-180°
        Data coordinates are pixels with b l or b r corner being 0,0
        """
        super(MercatorTileCalc, self).__init__()
        self.name = name
        self.pixel_shape = pixel_shape
        self.zero_point = zero_point
        self.pixel_rez = pixel_rez
        self.tile_shape = tile_shape

        assert(pixel_rez.dy > 0.0)        # FIXME: what if pixel_rez.dy < 0? can we handle this reliably?
        assert(pixel_rez.dx > 0.0)

        h,w = pixel_shape
        zy,zx = zero_point
        # below < 0, above >0
        # h = above - below
        # zy + above = h
        # below = -zy
        pxbelow = float(-zy)
        pxabove = float(h) - float(zy)
        # r > 0, l < 0
        # w = r - l
        # zx + r = w
        # l = -zx
        pxright = float(w) - float(zx)
        pxleft = float(-zx)

        self.extents_box = box(
            b = pxbelow * pixel_rez.dy,
            t = pxabove * pixel_rez.dy,
            l = pxleft * pixel_rez.dx,
            r = pxright * pixel_rez.dx
        )

        self.tiles_avail = (h/tile_shape[0], w/tile_shape[1])

        # FIXME: for now, require image size to be a multiple of tile size, else we have to deal with partial tiles!
        assert(h % tile_shape[0]==0)
        assert(w % tile_shape[1]==0)


    def visible_tiles(self, visible_geom, extra_tiles_box = box(0,0,0,0)):
        """
        given a visible world geometry and sampling, return (sampling-state, [box-of-tiles-to-draw])
        sampling state is WELLSAMPLED/OVERSAMPLED/UNDERSAMPLED
        tiles are specified as (iy,ix) integer pairs
        extra_box value says how many extra tiles to include around each edge
        """
        V = visible_geom
        X = extra_tiles_box  # FUTURE: extra_geom_box specifies in world coordinates instead of tile count
        E = self.extents_box
        Z = self.pixel_rez

        # convert world coords to pixel coords
        py0, px0 = self.extents_box.b, self.extents_box.l

        # pixel view b
        pv = box(
            b = (V.b - E.b)/Z.dy,
            l = (V.l - E.l)/Z.dx,
            t = (V.t - E.b)/Z.dy,
            r = (V.r - E.l)/Z.dx
        )

        # number of tiles wide and high we'll absolutely need
        th,tw = self.tile_shape
        nth = int(np.ceil((pv.t - pv.b) / th))
        ntw = int(np.ceil((pv.r - pv.l) / tw))

        # first tile we'll need is (tiy0,tix0)
        tiy0 = int(np.floor(pv.b / th))
        tix0 = int(np.floor(pv.l / tw))

        # now add the extras
        if X.b>0:
            tiy0 -= int(X.b)
            nth += int(X.b)
        if X.l>0:
            tix0 -= int(X.l)
            ntw += int(X.l)
        if X.t>0:
            nth += int(X.t)
        if X.r>0:
            ntw += int(X.r)

        # truncate to the available tiles
        if tix0<0:
            ntw += tix0
            tix0 = 0
        if tiy0<0:
            nth += tiy0
            tiy0 = 0

        ath,atw = self.tiles_avail
        xth = ath - (tiy0 + nth)
        if xth < 0:  # then we're asking for tiles that don't exist
            nth += xth  # trim it back
        xtw = atw - (tix0 + ntw)
        if xtw < 0:  # likewise with tiles wide
            ntw += xtw

        # FIXME: compare visible dx/dy versus tile dx/dy to determine over/undersampledness
        overunder = self.WELLSAMPLED

        tileset = box(
            b = tiy0,
            l = tix0,
            t = tiy0 + nth,
            r = tix0 + ntw
        )

        return overunder, tileset


    def tile_pixels(self, data, tiy, tix):
        """
        extract pixel data for a given tile
        """
        return data[
               tiy*self.tile_shape[0]:(tiy+1)*self.tile_shape[0],
               tix*self.tile_shape[1]:(tix+1)*self.tile_shape[1]
               ]







class FlatFileTileSet(object):
    """
    A lazy-loaded image which can be mapped to a texture buffer and drawn on a polygon
    Represents a single x-y coordinate range at a single level of detail
    Will map data into GL texture memory for rendering

    Tiles have
    - extents in the mercator-meters space
    - a texture map
    - later: a shader which may be shared
    """
    data = None
    path = None
    _active = None     # {(y,x): (buffer,texture), ...}
    _calc = None  # calculator

    def __init__(self, path, element_dtype, shape, zero_point, pixel_rez, tile_shape=(DEFAULT_TILE_HEIGHT, DEFAULT_TILE_WIDTH)):
        """
        map the file as read-only
        chop it into data tiles
        tiles are set up such that tile (0,0) starts with zero_point and goes up and to the r

        """
        super(FlatFileTileSet, self).__init__()
        self.data = np.memmap(path, element_dtype, 'r', shape=shape)
        self._calc = MercatorTileCalc(name=path, pixel_shape=shape, zero_point=zero_point, pixel_rez=pixel_rez, tile_shape=tile_shape)
        self.path = path
        self._active = {}


    def __getitem__(self, tileyx):
        try:
            return self._active[tuple(tileyx)]
        except KeyError as unavailable:
            return self.activate(tileyx)


    def activate(self, tile_yx):
        """
        load a texture map from this data
        return ( box, texture-id )
        """
        # FUTURE: implement using glGenBuffers() and use a shader to render
        texid = glGenTextures(1)

        # offset within the array
        tile_y, tile_x = tile_yx
        th,tw = self._calc.tile_shape
        ys = (tile_y*th)*self._calc.zero_point[0]
        xs = (tile_x*tw)*self._calc.zero_point[1]
        ye = ys + th
        xe = xs + tw
        npslice = self.data[ys:ye, xs:xe]

        # FIXME: temporarmily require that textures aren't odd sizes
        assert(xe<=self.data.shape[1])
        assert(ye<=self.data.shape[0])

        glBindTexture(GL_TEXTURE_BUFFER, texid)
        glTexSubImage2D()

        self._active[(tile_y, tile_x)] = texid

        # # start working with this buffer
        # glBindBuffer(GL_COPY_WRITE_BUFFER, buffer_id)
        # # allocate memory for it
        # glBufferData(GL_COPY_WRITE_BUFFER, , size, )
        # # borrow a writable pointer
        # pgl = glMapBuffer(, )
        # # make a numpy ndarray wrapper that targets that memory location
        #
        # # copy the data from the memory mapped file
        # # if there's not enough data in the file, fill with NaNs first
        # if npslice.shape != npgl.shape:
        #     npgl[:] = np.nan(0)
        #
        # if not transform_func:
        #     # straight copy
        #     np.copyto(npgl, npslice, casting='unsafe')
        # else:
        #     # copy through transform function
        #
        # # let GL push it to the GPU
        #glUnmapBuffer(GL_COPY_WRITE_BUFFER)


    def deactivate(self, tile_y, tile_x):
        """
        release a given
        """
        k = (tile_y, tile_x)
        t = self._active[k]
        del self._active[k]

        glDeleteTextures([t])


class TextureTileLayer(Layer):
    """
    A layer represented as an array of quads with textures on them
    """
    pass





class ColormapTiles(TextureTileLayer):
    """
    Mercator-projected geographic layer with one or more levels of detail.
    Coarsest level of detail (1° typically) is always expected to be available for fast zoom.

    """
    _tileset = None
    _image = None
    _shape = None

    def __init__(self, path, zero_point=None, pixel_rez=None):
        """

        """
        super(ColormapTiles, self).__init__()
        if path.lower().endswith('.json'):
            from json import load
            with open(path, 'rt') as fp:
                info = load(fp)
                globals().update(info)  # FIXME: security
        self._image = im = Image.open(path)
        # image is top-left to bottom-right, remember this when loading textures
        im.load()
        l,t,r,b = im.getbbox()
        w = r-l
        h = b-t
        self._shape = (h,w)



    def paint(self, geom, fast=False):
        return True

    def render(self, geom, *more_geom):
        """
        in offline GL, make a drawlist that nicely handles the world geometry requested
        """

        return True

    def purge(self, geom, *more_geom):
        return False

    def probe_point_xy(self, x, y):
        raise NotImplementedError()

    def probe_point_geo(self, lat, lon):
        raise NotImplementedError()

    def probe_shape(self, geo_shape):
        raise NotImplementedError()


    # def tileseq_in_area(self, index_bltr):
    #     """
    #     yield the sequence of tiles in a given rectangular tileseq_in_area
    #     """
    #     pass
    #
    # def tileseq_visible(self, data_bltr):
    #     """
    #     given data coordinates, determine which tiles are on the canvas
    #     """
    #     pass
    #



class TestLayer(Layer):
    def paint(self, *args):
        glColor3f(0.0, 0.0, 1.0)
        glRectf(-5, -5, 5, 5)
        glColor3f(1.0, 0.0, 0.0)
        glBegin(GL_LINES)
        glVertex3f(0, 0, 0)
        glVertex3f(20, 20, 0)






class CsGlWidget(QGLWidget):
    layers = None

    def __init__(self, parent=None):
        super(CsGlWidget, self).__init__(parent)
        self.layers = [TestLayer()]

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT)
        for layer in self.layers:
            layer.paint()

        glEnd()

    def resizeGL(self, w, h):
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(-50, 50, -50, 50, -50.0, 50.0)
        glViewport(0, 0, w, h)

    def initializeGL(self):
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        print(glGetString(GL_VERSION))
        print("GLSL {}".format(glGetIntegerv(GL_SHADING_LANGUAGE_VERSION)))



class MainWindow(QMainWindow):

    significant = pyqtSignal(str)

    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)
        # self.windowTitleChanged.connect(self.onWindowTitleChange)
        self.setWindowTitle("gltiles")
        # layout = QStackedLayout()
        widget = QTabWidget()
        # things = [QDateEdit, QLabel, QDial, QDoubleSpinBox, QSpinBox, QProgressBar, QSlider, QRadioButton, QTimeEdit, QFontComboBox, QLineEdit]
        things = [CsGlWidget, QTextEdit]
        for w in things: 
            if w is QLabel:
                wid = QLabel('hola')
                font = wid.font()
                font.setPointSize(32)
                wid.setFont(font)
                wid.setAlignment(Qt.AlignHCenter)
            elif w is QLineEdit:
                q = QLineEdit()
                q.setPlaceholderText("I am text hear me roar")
                q.returnPressed.connect(self.return_pressed)
                q.selectionChanged.connect(self.selection_changed)
                q.textChanged.connect(self.text_changed)
                q.textEdited.connect(self.text_edited)
                self.line = q
                wid = q
            else:
                wid = w()
            # layout.addWidget(wid)
            widget.addTab(wid, str(w))

        self.setCentralWidget(widget)
        self.significant.connect(self.on_my_signal)

    def contextMenuEvent(self, e):
        print("context menu")
        # maj,min = glGetIntegerv(GL_MAJOR_VERSION), glGetIntegerv(GL_MINOR_VERSION)
        # print("OpenGL {}.{}".format(maj,min))

        super(MainWindow, self).contextMenuEvent(e)  # can also use e.accept() or e.ignore()

    def return_pressed(self):
        print('return pressed')
        self.line.setText("BOOM")

    def selection_changed(self):
        print('selection changed')

    def text_edited(self):
        print("text edited to " + repr(self.line.text()))

    def text_changed(self):
        print("text changed to " + repr(self.line.text()))

    def on_golden_pond(self, a):
        print(a)

    def on_button_pressed(self):
        print("bort")
        self.significant.emit('-bort-')

    def on_my_signal(self, s):
        print('there has been {0:s}'.format(s))


    # def onWindowTitleChange(self, s):
    #     print(s)


if __name__=='__main__':
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    app.exec_()
