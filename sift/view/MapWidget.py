#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
.py
~~~

PURPOSE


REFERENCES


REQUIRES


:author: R.K.Garcia <rayg@ssec.wisc.edu>
:copyright: 2014 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""
from PyQt4.QtCore import QTimer
from vispy import app, gloo, scene
import numpy as np
from vispy.util.transforms import translate, ortho
from numba import jit

from sift.common import box, MAX_EXCURSION_X, vue



# from sift.view.Program import GlooRGBTile

__author__ = 'rayg'
__docformat__ = 'reStructuredText'

import logging

LOG = logging.getLogger(__name__)


class MapWidgetActivity(object):
    """
    Major mouse activities represented as objects, to simplify main window control logic
    Right now this is crude and we'll eventually run out of road and have to rethink it
    For now it's a good leg up.
    Eventually we want something more like a Behavior wired in at the Designer level??

    The Map window has an activity which is the main thing it's doing
    return None for "I remain status quo"
    return False for "dismiss me"
    return a new activity for "send to this guy"
    """
    main = None   # main map widget we serve

    def __init__(self, main):
        super(MapWidgetActivity, self).__init__()
        self.main = main

    def layer_paint_parms(self):
        """
        return additional keyword parameters to be sent to layers when they're painting
        """
        return {}

    def mouseReleaseEvent(self, event):
        return None

    def mouseMoveEvent(self, event):
        return None

    def mousePressEvent(self, event):
        return None

    def wheelEvent(self, event):
        return None


class UserPanningMap(MapWidgetActivity):
    """
    user mouses down
    user drags map
        click and drag OR
        Mac: scroll surface option
    user mouses up
    """
    def mouseReleaseEvent(self, event):
        """
        user is done zooming
        go back to idle
        draw at higher resolution (not fast-draw)
        """
        print("done panning")
        LOG.info("new viewport after pan: {0!r:s}".format(self.main.viewport))
        return False  # we're done, dismiss us

    def mouseMoveEvent(self, event):
        """
        change the visible region
        invalidate the view
        """
        x, y = event.x(), event.y()
        pdx = self.lx - x
        # GL coordinates are reversed from screen coordinates
        pdy = y - self.ly
        self.main.panViewport(pdy=pdy, pdx=pdx)
        # self.main.updateGL()  # repaint() is faster if we need it
        self.lx, self.ly = x, y
        # print("pan dx={0} dy={1}".format(pdx,pdy))
        return None

    def mousePressEvent(self, event):
        """
        Idling probably sent this our way, use it to note where we're starting from
          Also optionally change cursors
        """
        print("mouse down, starting pan")
        self.lx, self.ly = event.x(), event.y()
        return None


class UserZoomingMap(MapWidgetActivity):
    """
    user starts zooming
        scroll wheel OR
        chording with keyboard and mouse
    user zooms inward
    user zooms outward
    user ends zooming
    """
    def wheelEvent(self, event):
        # event.accept()
        # pos = event.pos()
        delta = event.delta()
        self.main.zoomViewport(delta)
        return None

    def mouseMoveEvent(self, event):
        # FIXME: does it work to drop the zooming by way of a mouse move?
        LOG.info("new viewport after zoom: {0!r:s}".format(self.main.viewport))
        return False

    def mousePressEvent(self, event):
        LOG.info("new viewport after zoom: {0!r:s}".format(self.main.viewport))
        return False

    def mouseReleaseEvent(self, event):
        LOG.info("new viewport after zoom: {0!r:s}".format(self.main.viewport))
        return False


class UserZoomingRegion(MapWidgetActivity):
    """
    user starts region selection
        click and drag with tool OR
        click and drag middle mouse button??
    user continues selecting box region
    user finishes selecting region
    """
    def mouseReleaseEvent(self, event):
        """
        user is done zooming
        go back to idle
        draw at higher resolution (not fast-draw)
        """
        return False

    def mouseMoveEvent(self, event):
        return None

    def mousePressEvent(self, event):
        return False  # we should never get this


class Idling(MapWidgetActivity):
    """
    This is the default behavior we do when nothing else is going on
    :param Behavior:
    :return:
    """

    def mouseMoveEvent(self, event):
        # FIXME: send world coordinates to cursor coordinate content probe
        # print("mousie") # yeah this works
        return None

    def mousePressEvent(self, event):
        """
        Drag with left mouse button to pan
        Drag with right mouse button to zoom (to point?)
        Drag with middle button for rectangle zoom? Or drag with modifier key to zoom?
        """
        return UserPanningMap(self.main)  # FIXME more routes to route

    def wheelEvent(self, event):
        return UserZoomingMap(self.main)


class Animating(Idling):
    """
    When we're doing an animation cycle
    :param Behavior:
    :return:
    """


class SIFTMainMapCanvas(scene.SceneCanvas):
    pass


class SIFTMainMapWidget(app.Canvas):

    # signals
    # viewportDidChange = pyqtSignal(box)

    # members
    _activity_stack = None  # Activity object stack which we push/pop for primary activity; activity[-1] is what we're currently doing
    _animation_timer = None  # animation cycling
    _animating = False
    _frame_number = 0
    drawing_plan = None  # LayerDrawingPlan we're currently displaying, last on top
    viewport = None  # box with world coordinates of what we're showing

    _testtile = None
    _deferred_render_layers = None  # FIXME replace with a task queue
    _deferred_render_timer = None

    def __init__(self, **kwargs):
        super(SIFTMainMapWidget, self).__init__(**kwargs)

        self._activity_stack = [Idling(self)]
        self._deferred_render_layers = []

        aspect = float(self.size[1]) / float(self.size[0])
        # self.viewport = vp = box(l=-4, r=4, b=-4*aspect, t=4*aspect)
        rad = MAX_EXCURSION_X/2
        self.viewport = vp = box(l=-rad, r=rad, b=-rad*aspect, t=rad*aspect)

        # Handle transformations
        self.init_transforms()
        self.update_proj()

        gloo.set_clear_color((0.2, 0.2, 0.2, 1))
        gloo.set_state(depth_test=True)

        # FIXME: drawing plan will get moved outside the constructor;
        # application will construct it and it will be managed either by the document or from the document
        # self.drawing_plan = test_layers(self.model, self.view)

        self._animation_timer = app.Timer(1.0/10.0, connect=self.next_frame)
        # self._timer.start()

        self.show()

    def next_frame(self, event=None, frame_number=None):
        """
        skip to the frame (from 0) or increment one frame and update
        typically this is run by self._animation_timer
        :param frame_number: optional frame to go to, from 0
        :return:
        """
        frame = frame_number if isinstance(frame_number, int) else self._frame_number + 1
        self._frame_number = frame
        self.update()

    def set_animating(self, animating=True):
        if animating is None:
            animating = not self._animating
        if animating and not self._animating:
            self._animating = True
            self._animation_timer.start()
            LOG.info("animation on")
        elif not animating and self._animating:
            self._animation_timer.stop()
            self._frame_number = 0
            self._animating = False
            LOG.info("animation off")

    @property
    def activity(self):
        return self._activity_stack[-1]

    @jit
    def zoomViewport(self, pdz=None, wdz=None):
        if pdz is not None:
            pw, ph = self.size
            wh, ww = self.viewport.t - self.viewport.b, self.viewport.r - self.viewport.l
            wdy, wdx = float(pdz)/ph*wh, float(pdz)/pw*ww
        # aspect = float(self.size[0]) / float(self.size[1])  # x/y
        # aspect = ww/wh  # x/y
        b=self.viewport.b+wdy
        t=self.viewport.t-wdy
        # c = (self.viewport.r + self.viewport.l)/2.0
        # xcursion = ww/wh * wdy
        # l,r = c - xcursion, c + xcursion
        wdx = ww/wh * wdy
        l = self.viewport.l+wdx
        r = self.viewport.r-wdx

        # LOG.info('wdx={} aspect={}'.format(wdx, aspect))
        # nvp = box(b=self.viewport.b+wdy, t=self.viewport.t-wdy, l=self.viewport.l+wdx, r=self.viewport.r-wdx)
        nvp = box(b=b, t=t, l=l, r=r)
        # print("pan viewport {0!r:s} => {1!r:s}".format(self.viewport, nvp))
        self.viewport = nvp
        self.update_proj()
        # LOG.info("new viewport: {0!r:s}".format(nvp))
        # self.viewportDidChange.emit(nvp)
        self.update()

    def panViewport(self, pdy=None, pdx=None, wdy=None, wdx=None):
        """
        displace view by pixel or world coordinates
        does not queue screen update
        :param pdy: displacement in pixels, y:int
        :param pdx: displacement in pixels, x:int
        :param wdy: displacement in world y:float
        :param wdx: displacement in world x:float
        :return: new world viewport
        """
        # print(" viewport pan requested {0!r:s}".format((pdy,pdx,wdy,wdx)))
        if (pdy, pdx) is not (None, None):
            pw, ph = self.size
            # ph, pw = float(s.height()), float(s.width())
            wh, ww = self.viewport.t - self.viewport.b, self.viewport.r - self.viewport.l
            wdy, wdx = float(pdy)/ph*wh, float(pdx)/pw*ww
        elif (wdy, wdx) is (None, None):
            return self.viewport
        # print("pan {}y {}x".format(pdy, pdx))
        nvp = box(b=self.viewport.b+wdy, t=self.viewport.t+wdy, l=self.viewport.l+wdx, r=self.viewport.r+wdx)
        # print("pan viewport {0!r:s} => {1!r:s}".format(self.viewport, nvp))
        self.viewport = nvp
        self.update_proj()  # recalculate projection matrix
        # LOG.info("new viewport: {0!r:s}".format(nvp))
        # self.viewportDidChange.emit(nvp)
        self.update()
        return self.viewport

    #
    # GLOO
    #

    def init_transforms(self):
        self.theta = 0
        self.phi = 0
        self.view = translate((0, 0, -5), dtype=np.float32)
        self.model = np.eye(4, dtype=np.float32)
        self.projection = np.eye(4, dtype=np.float32)
        if self._testtile:
            self._testtile.set_mvp(self.model, self.view, self.projection)

    def update_transforms(self, event):
        # self.theta += .1
        # self.phi += .1
        # self.model = np.dot(rotate(self.theta, (0, 0, 1)),
        #                     rotate(self.phi, (0, 1, 0)))
        # self._testtile.set_mvp(self.model)
        self.update()

    def on_resize(self, event):
        # FIXME: maintain aspect ratio
        self.update_proj()

    def update_proj(self, event=None):
        if event is not None:
            gloo.set_viewport(0, 0, *event.physical_size)
        else:
            gloo.set_viewport(0, 0, self.physical_size[0], self.physical_size[1])
        vp = self.viewport
        # self.projection = perspective(45.0, self.size[0] /
        #                               float(self.size[1]), 2.0, 10.0)
        aspect = float(self.size[1]) / float(self.size[0])
        #self.projection = ortho(-4, 4, -4*aspect, 4*aspect, -10, 10)
        # FIXME: Z is backwards from documentation based on results. Negative first, positive second makes Z positive towards the viewer
        self.projection = ortho(
            vp.l, vp.r,
            vp.b, vp.t,
            -10000000, 10000000
        )
        if self._testtile:
            self._testtile.set_mvp(projection=self.projection)

    def render_layers(self, layers=None):
        layers = layers or self._deferred_render_layers
        LOG.info('re-rendering {} layers'.format(len(layers)))
        if not layers:
            return
        vp = self._vueport()
        for layer in layers:
            layer.render(vp)
        self._deferred_render_layers = []
        self._deferred_render_timer = None
        self.update()

    @jit
    def _vueport(self):
        w,h = self.size
        dx = (self.viewport.r - self.viewport.l)/float(w)
        dy = (self.viewport.t - self.viewport.b)/float(h)
        return vue(l=self.viewport.l, r=self.viewport.r, b=self.viewport.b, t=self.viewport.t, dx=dx, dy=dy)

    def on_draw(self, event):
        gloo.clear()
        mvp = self.model, self.view, self.projection
        visible_geom = self._vueport()
        render_candidates = []
        # LOG.info('drawing {} layers'.format(len(self.drawing_plan)))
        for layer in self.drawing_plan(self._frame_number if self._animating else None):
            if layer.paint(visible_geom, mvp): # then we should re-render
                render_candidates.append(layer)
        if render_candidates:
            # def rerender(self=self, layers=render_candidates):
            #     self.render_layers(layers)
            # nqueued = len(self._deferred_render_layers)
            for cand in render_candidates:
                if cand not in self._deferred_render_layers:
                    self._deferred_render_layers.append(cand)
            # self._deferred_render_layers += render_candidates
            if self._deferred_render_timer is not None:
                self._deferred_render_timer.stop()
            # if nqueued==0:
            self._deferred_render_timer = QTimer.singleShot(250, self.render_layers)  # FIXME: defer this if we continue drawing
            LOG.debug('{0:d} layers queued to be re-rendered'.format(len(render_candidates)))

        if self._testtile:
            self._testtile.draw()

    # def on_compile(self):
    #     vert_code = str(self.vertEdit.toPlainText())
    #     frag_code = str(self.fragEdit.toPlainText())
    #     self.canvas.program.set_shaders(vert_code, frag_code)


    def on_key_press(self, key):
        # print('down', repr(key))
        pass

    def on_key_release(self, key):
        # print('up', repr(key))
        if key.text=='a':  # toggle whether to animate or not
            self.set_animating(None)  # toggle

    def on_mouse_release(self, event):
        event = event.native  # FIXME: stop using .native, send the vispy event and refactor the Activities
        newact = True
        while newact is not None:
            newact = self.activity.mouseReleaseEvent(event)
            if newact is None:
                break
            if newact is False:
                self._activity_stack.pop()
                continue
            assert(isinstance(newact, MapWidgetActivity))
            self._activity_stack.append(newact)

    def on_mouse_move(self, event):
        # print("mouse_move")
        event = event.native
        newact = True
        while newact is not None:
            newact = self.activity.mouseMoveEvent(event)
            if newact is None:
                return
            if newact is False:
                self._activity_stack.pop()
                continue
            assert(isinstance(newact, MapWidgetActivity))
            self._activity_stack.append(newact)

    def on_mouse_press(self, event):
        event = event.native
        newact = True
        while newact is not None:
            newact = self.activity.mousePressEvent(event)
            if newact is None:
                return
            if newact is False:
                self._activity_stack.pop()
                continue
            assert(isinstance(newact, MapWidgetActivity))
            self._activity_stack.append(newact)

    def on_mouse_wheel(self, event):
        event = event.native
        newact = True
        while newact is not None:
            newact = self.activity.wheelEvent(event)
            if newact is None:
                return
            if newact is False:
                self._activity_stack.pop()
                continue
            assert(isinstance(newact, MapWidgetActivity))
            self._activity_stack.append(newact)