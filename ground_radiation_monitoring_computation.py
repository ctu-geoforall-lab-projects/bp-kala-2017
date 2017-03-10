# -*- coding: utf-8 -*-
import os

from PyQt4.QtCore import QSettings, QTranslator, qVersion, QCoreApplication, Qt, QFileInfo
from PyQt4.QtGui import QComboBox, QAction, QIcon, QToolButton, QFileDialog
from qgis.core import QgsMapLayerRegistry, QgsMapLayer, QGis, QgsPoint, QgsRaster, QgsProject,  QgsProviderRegistry, QgsDistanceArea
from qgis.utils import QgsMessageBar, iface
from qgis.gui import QgsMapLayerComboBox,QgsMapLayerProxyModel
from osgeo import gdal, ogr
from math import ceil
from array import array

from PyQt4 import QtGui, uic
from PyQt4.QtCore import pyqtSignal

class GroundRadiationMonitoringComputation:
    def exportRasterValues(self, rasterLayerId, trackLayerId, fileName, vertexDist):
        """Export sampled raster values to output CSV file.

        :rasterLayerId: input raster layer (QgsRasterLayer)
        :trackLayerId: linestring vector layer to be sampled (QgsVectorLayer)
        :fileName: file descriptor of output CVS file
        :vertexDist: user defined distance between new vertices
        """
        try:
            csvFile = open(fileName, 'wb')
        except IOError as e:
            return e

        rasterLayer = QgsMapLayerRegistry.instance().mapLayer(rasterLayerId)
        trackLayer = QgsMapLayerRegistry.instance().mapLayer(trackLayerId)

        # get coordinates of vertices based on user defined sample segment length
        vectorX, vectorY = self.getCoor(rasterLayer, trackLayer, vertexDist)
        
        for X,Y in zip(vectorX,vectorY):
            value = rasterLayer.dataProvider().identify(QgsPoint(X,Y),QgsRaster.IdentifyFormatValue).results()
            csvFile.write('{val}{linesep}'.format(val=value.values()[0], linesep=os.linesep))

        # close output file
        csvFile.close()
        return None

    def getCoor(self, rasterLayer, trackLayer, vertexDist):
        """Get coordinates of vertices of sampled track.

        :rasterLayer: input raster layer (QgsRasterLayer)
        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        :vertexDist: user defined distance between new vertices
        """
        distanceBetweenVertices = float(vertexDist.replace(',', '.'))

        # declare arrays of coordinates of vertices
        vertexX = array('f',[])
        vertexY = array('f',[])

        # get coordinates of vertices of uploaded track layer
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            polyline = feature.geometry().asPolyline()
            pointCounter = 0
            vertexX.append(polyline[0][0])
            vertexY.append(polyline[0][1])
            while pointCounter < (len(polyline)-1):
                point1 = polyline[pointCounter]
                point2 = polyline[pointCounter+1]
                distance = self.distance(point1,point2)

                # check whether the input distance between vertices is longer then the distance between points
                if distance > distanceBetweenVertices:
                    newX, newY = self.sampleLine(point1,point2, distance, distanceBetweenVertices)
                    vertexX.extend(newX)
                    vertexY.extend(newY)
                else:
                    vertexX.append(point2[0])
                    vertexY.append(point2[1])
                pointCounter = pointCounter + 1
 
        # returns coordinates of all vertices of track       
        return vertexX, vertexY        

    def distance(self, point1, point2):
        """Compute distance between points in metres.

        :point1: first point
        :point2: secound point
        """

        distance = QgsDistanceArea()
        distance.setEllipsoid('WGS84')
        distance.setEllipsoidalMode(True)
        d = distance.measureLine(QgsPoint(point1), QgsPoint(point2))
        return d

    def sampleLine(self,point1, point2, dist, distBetweenVertices):
        """Sample line between two points to segments of user selected length.

        Compute coordinates of new vertices.

        :point1: first point of line
        :point2: last point of line
        :dist: length of line in metres
        :distBetweenVertices: length of segment selected by user
        """

        # number of vertices, that should be added between 2 points
        vertexQuantity = ceil(dist / distBetweenVertices) - 1

        # if modulo of division of line length and 1 segment length is not 0,
        # point where last complete segment ends is computed
        if dist % distBetweenVertices != 0:
            shortestSegmentRel = (dist % distBetweenVertices) / dist
            lastPointX = point2[0] - (point2[0] - point1[0]) * shortestSegmentRel
            lastPointY = point2[1] - (point2[1] - point1[1]) * shortestSegmentRel
            vectorX = lastPointX - point1[0]
            vectorY = lastPointY - point1[1] 
        else:
            lastPointX = point2[0]
            lastPointY = point2[1]
            vectorX = point2[0] - point1[0]
            vectorY = point2[1] - point1[1]

        # compute addition to coordinates with size of 1 segment    
        addX = vectorX / vertexQuantity
        addY = vectorY / vertexQuantity

        # declare arrays for newly computed points
        newX = array('f',[])
        newY = array('f',[])

        # compute new points
        for n in range(1,int(vertexQuantity)):
            newX.append((point1[0]+n*addX))
            newY.append((point1[1]+n*addY))
        newX.append(lastPointX)
        newY.append(lastPointY)

        return newX, newY