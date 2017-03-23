# -*- coding: utf-8 -*-
import os

from PyQt4.QtCore import QSettings, QTranslator, qVersion, QCoreApplication, Qt, QFileInfo, pyqtSignal, QThread
from PyQt4.QtGui import QComboBox, QAction, QIcon, QToolButton, QFileDialog
from qgis.core import QgsMapLayerRegistry, QgsMapLayer, QGis, QgsPoint, QgsRaster, QgsProject,  QgsProviderRegistry, QgsDistanceArea
from qgis.utils import QgsMessageBar, iface
from qgis.gui import QgsMapLayerComboBox,QgsMapLayerProxyModel
from osgeo import gdal, ogr
from math import ceil
from array import array

from qgis.core import QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsVectorFileWriter
from PyQt4.QtCore import QVariant  

import osgeo.ogr as ogr
import osgeo.osr as osr
import csv

class GroundRadiationMonitoringComputation(QThread):
    # set length measurement
    length = QgsDistanceArea()
    length.setEllipsoid('WGS84')
    length.setEllipsoidalMode(True)
    
    # set signals
    computeEnd = pyqtSignal()
    computeStat = pyqtSignal(int)
    computeProgress = pyqtSignal(str)

    def __init__(self,  rasterLayerId, trackLayerId, fileName, shpFileName, vertexDist):
        QThread.__init__(self)
        self.rasterLayerId = rasterLayerId
        self.trackLayerId = trackLayerId
        self.fileName = fileName
        self.shpFileName = shpFileName
        self.vertexDist = vertexDist
        
    def run(self):
        """Run compute thread."""
        self.exportRasterValues(self.rasterLayerId, 
                                self.trackLayerId, 
                                self.fileName, 
                                self.shpFileName, 
                                self.vertexDist)

    def exportRasterValues(self, rasterLayerId, trackLayerId, fileName, shpFileName, vertexDist):
        """Export sampled raster values to output CSV file.

        :rasterLayerId: input raster layer (QgsRasterLayer)
        :trackLayerId: linestring vector layer to be sampled (QgsVectorLayer)
        :fileName: file descriptor of output CVS file
        :shpFileName: file descriptor of output shp file
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
        
        self.computeProgress.emit(u'Getting raster values...')
        i = 0
        rows = len(vectorX)
        for X,Y in zip(vectorX,vectorY):
            i = i + 1
            self.computeStat.emit(float(i)/rows * 100)

            value = rasterLayer.dataProvider().identify(QgsPoint(X,Y),QgsRaster.IdentifyFormatValue).results()
            csvFile.write('{val}{linesep}'.format(val=value.values()[0], linesep=os.linesep))

        # close output file
        csvFile.close()
        self.createShp(vectorX, vectorY, trackLayer, shpFileName)
        self.computeEnd.emit()
        
    def getCoor(self, rasterLayer, trackLayer, vertexDist):
        """Get coordinates of vertices of sampled track.

        :rasterLayer: input raster layer (QgsRasterLayer)
        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        :vertexDist: user defined distance between new vertices
        """

        distanceBetweenVertices = float(vertexDist.replace(',', '.'))

        # declare arrays of coordinates of vertices
        vertexX = array('d',[])
        vertexY = array('d',[])

        # get coordinates of vertices of uploaded track layer
        i = 1
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            self.computeProgress.emit(u'Sampling track ({})...'.format(i))
            i = i + 1

            polyline = feature.geometry().asPolyline()
            pointCounter = 0
            vertexX.append(polyline[0][0])
            vertexY.append(polyline[0][1])
            amount = len(polyline)
            while pointCounter < (amount-1):
                
                self.computeStat.emit(float(pointCounter)/amount * 100)
                
                point1 = polyline[pointCounter]
                point2 = polyline[pointCounter+1]
                distance = GroundRadiationMonitoringComputation.length.measureLine(QgsPoint(point1), QgsPoint(point2))

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
        lastPointX = lastPointY = None
        if dist % distBetweenVertices != 0:
            shortestSegmentRel = (dist % distBetweenVertices) / dist
            lastPointX = point2[0] - (point2[0] - point1[0]) * shortestSegmentRel
            lastPointY = point2[1] - (point2[1] - point1[1]) * shortestSegmentRel
            vectorX = lastPointX - point1[0]
            vectorY = lastPointY - point1[1] 
        else:
            vectorX = point2[0] - point1[0]
            vectorY = point2[1] - point1[1]

        # compute addition to coordinates with size of 1 segment    
        addX = vectorX / vertexQuantity
        addY = vectorY / vertexQuantity

        # declare arrays for newly computed points
        newX = array('d',[])
        newY = array('d',[])

        # compute new points
        for n in range(1,int(vertexQuantity)):
            newX.append((point1[0]+n*addX))
            newY.append((point1[1]+n*addY))
        if lastPointX:
            newX.append(lastPointX)
            newY.append(lastPointY)
        newX.append(point2[0])
        newY.append(point2[1])

        return newX, newY

    def createShp(self, vectorX, vectorY, trackLayer, shpFileName):
        """Create ESRI shapefile and write new points. 

        :vectorX: X coordinates of points
        :vectorY: Y coordinates of points
        :trackLayer: layer to get coordinate system from
        :shpFileName: destination to save shapefile and csv file of coordinates of new points
 
        """
        
        self.computeProgress.emit(u'Creating shapefile...')
        # save csv with coordinates of new points
        cannotWrite = True
        i = 1
        coorFileName = '{}_coor.csv'.format(shpFileName.split('.')[0])
        while cannotWrite == True:
            try:
                csvFile = open('{f}'.format(f=coorFileName), 'wb')
                cannotWrite = False
            except IOError:
                coorFileName = '{}_coor({}).csv'.format(shpFileName.split('.')[0],i)
                i = i + 1

        csvFile.write('X\tY{linesep}'.format(linesep=os.linesep))
        for X,Y in zip(vectorX,vectorY):
            csvFile.write('{X}\t{Y}{linesep}'.format(X=X, Y = Y,linesep=os.linesep))
        csvFile.close()

        reader = csv.DictReader(open('{f}'.format(f=coorFileName),"rb"),
                                delimiter='\t',
                                quoting=csv.QUOTE_NONE)

        # set up the shapefile driver
        driver = ogr.GetDriverByName("ESRI Shapefile")
        
        # create the data source
        data_source = driver.CreateDataSource('{f}'.format(f=shpFileName))

        # create the spatial reference
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(trackLayer.crs().authid()[5:]))

        # create the layer
        layer = data_source.CreateLayer("{}".format(shpFileName), srs, ogr.wkbPoint)

        # Add the fields we're interested in
        layer.CreateField(ogr.FieldDefn("X", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("Y", ogr.OFTReal))

        # Process the text file and add the attributes and features to the shapefile
        i = 0
        rows = len(vectorX)
        for row in reader:
            i = i + 1
            self.computeStat.emit(float(i)/rows * 100)
            # create the feature
            feature = ogr.Feature(layer.GetLayerDefn())
            # Set the attributes using the values from the delimited text file
            feature.SetField("X", row["X"])
            feature.SetField("Y", row["Y"])

            # Create the point geometry
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint(float(row["X"]), float(row["Y"]))

            # Set the feature geometry using the point
            feature.SetGeometry(point)
            # Create the feature in the layer (shapefile)
            layer.CreateFeature(feature)
            # Dereference the feature
            feature = None

        # Save and close the data source
        data_source = None
