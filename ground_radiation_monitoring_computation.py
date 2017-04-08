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
import codecs

class GroundRadiationMonitoringComputation(QThread):
    # set Gy/Sv coeficient
    COEFICIENT = 1
    
    # set length measurement
    length = QgsDistanceArea()
    length.setEllipsoid('WGS84')
    length.setEllipsoidalMode(True)
    
    # set signals
    computeEnd = pyqtSignal()
    computeStat = pyqtSignal(int,str)
    computeProgress = pyqtSignal()
    computeMessage = pyqtSignal(str,str,str)

    def __init__(self,  rasterLayerId, trackLayerId, reportFileName, csvFileName, shpFileName, vertexDist, speed, units):
        QThread.__init__(self)
        self.rasterLayerId = rasterLayerId
        self.trackLayerId = trackLayerId
        self.reportFileName = reportFileName
        self.csvFileName = csvFileName
        self.shpFileName = shpFileName
        self.vertexDist = vertexDist
        self.speed = speed
        self.units = units

    def run(self):
        """Run compute thread."""
        
        self.computeProgress.emit()
        
        self.abort = False

        rasterLayer = QgsMapLayerRegistry.instance().mapLayer(self.rasterLayerId)
        trackLayer = QgsMapLayerRegistry.instance().mapLayer(self.trackLayerId)
        trackName = QgsMapLayerRegistry.instance().mapLayer(self.trackLayerId).name()

        # get coordinates of vertices based on user defined sample segment length
        vertexX, vertexY, distances = self.getCoor(rasterLayer, trackLayer)

        if self.abort == True:
            return

        dose, index = self.createCsv(vertexX, vertexY, rasterLayer)

        if self.abort == True:
            return

        distance, time, maxDose, avgDose, totalDose = self.computeReport(vertexX, vertexY, dose, index, distances)

        if self.abort == True:
            return

        self.createReport(trackName, time, distance, maxDose, avgDose, totalDose)

        if self.abort == True:
            return

        self.createShp(vertexX, vertexY, trackLayer)

        if self.abort == True:
            return

        self.computeEnd.emit()
    
    def abortThread(self):
        self.abort = True
      
    def getCoor(self, rasterLayer, trackLayer):
        """Get coordinates of vertices of sampled track.

        :rasterLayer: input raster layer (QgsRasterLayer)
        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        """

        # declare arrays of coordinates of vertices and of distance between them
        vertexX = array('d',[])
        vertexY = array('d',[])
        distances = array('d',[])

        # get coordinates of vertices of uploaded track layer
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            
            if self.abort == True:
                    break


            polyline = feature.geometry().asPolyline()
            pointCounter = 0
            vertexX.append(polyline[0][0])
            vertexY.append(polyline[0][1])
            amount = len(polyline)
            while pointCounter < (amount-1):
                
                if self.abort == True:
                    break

                self.computeStat.emit(float(pointCounter)/amount * 10, u'(1/4) Sampling track...')
                
                point1 = polyline[pointCounter]
                point2 = polyline[pointCounter+1]
                distance = self.distance(point1, point2)
                distances.append(distance)

                # check whether the input distance between vertices is longer then the distance between points
                if distance > self.vertexDist and self.vertexDist != 0:
                    newX, newY = self.sampleLine(point1,point2, distance)
                    vertexX.extend(newX)
                    vertexY.extend(newY)
                else:
                    vertexX.append(point2[0])
                    vertexY.append(point2[1])
                pointCounter = pointCounter + 1
 
        # returns coordinates of all vertices of track   
        return vertexX, vertexY, distances

    def sampleLine(self,point1, point2, dist):
        """Sample line between two points to segments of user selected length.

        Compute coordinates of new vertices.

        :point1: first point of line
        :point2: last point of line
        :dist: length of line in metres
        """

        # number of vertices, that should be added between 2 points
        vertexQuantity = ceil(dist / float(self.vertexDist)) - 1

        # if modulo of division of line length and 1 segment length is not 0,
        # point where last complete segment ends is computed
        lastPointX = lastPointY = None
        if dist % self.vertexDist != 0:
            shortestSegmentRel = (dist % self.vertexDist) / dist
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

    def createCsv(self, vertexX, vertexY, rasterLayer):
        """Export sampled raster values to output CSV file.
        
        Prints error when CSV file cannot be opened for writing.

        :vertexX: X coordinates of points
        :vertexY: Y coordinates of points
        :rasterLayer: raster layer dose rate is exctracted from
        """
        try:
            csvFile = open(self.csvFileName, 'wb')
        except IOError as e:
            self.computeMessage.emit(u'Error', u'Unable open {} for writing. Reason: {}'.format(self.csvFileName, e),'CRITICAL')
            return

        csvFile.write(self.tr(u'X,Y,dosage{linesep}'.format(linesep = os.linesep)))

        rows = len(vertexX)
        
        # declare arrays for non-None dose rates and their indexes for use in total dosage computation
        dose = array('d',[])
        index = []
        
        i = 0
        for X,Y in zip(vertexX,vertexY):
            
            if self.abort == True:
                csvFile.close()
                break
            
            i = i + 1
            self.computeStat.emit(float(i)/rows * 100, u'(2/4) Getting raster values...')

            value = rasterLayer.dataProvider().identify(QgsPoint(X,Y),QgsRaster.IdentifyFormatValue).results()
            csvFile.write(self.tr(u'{valX},{valY},{val}{linesep}'.format(valX = X,
                                                                         valY = Y,
                                                                         val = value.values()[0], 
                                                                         linesep=os.linesep)))
            # get non-None dose rate values and their indexes
            if value.values()[0]:
                dose.append(value.values()[0])
                index.append(i-1)

        # close output file
        csvFile.close()
        
        return dose, index

    def distance(self, point1, point2):
        """Compute length between 2 QgsPoints.
        
        :point1: 1st point
        :point2: 2nd point
        """
        distance = GroundRadiationMonitoringComputation.length.measureLine(QgsPoint(point1[0],point1[1]), QgsPoint(point2[0],point2[1]))
        return distance

    def computeReport(self, vertexX, vertexY, dose, index, distances):
        """Compute statistics (main output of plugin).
        
        COEFICIENT for Gy/Sv ratio
        
        :vertexX: X coordinates of points
        :vertexY: Y coordinates of points
        :dose: list of dose rates on points
        :index: list of indexes indicating on what coordinates are raster values avaiable
        :distances: list of distances between vertices of non-sampled track
        """
        # COEFICIENT Gy/Sv
        coef = GroundRadiationMonitoringComputation.COEFICIENT
        
        # initialize variables
        maxDose = avgDose = totalDose = None
        time = [0,0,0]
        totalDistance = 0
        
        # total distance
        # distance = round(sum(distances)/1000,3)

        # total time
        # decTime = distance/float(self.speed)
        # hours = int(decTime)
        # minutes = int((decTime-hours)*60)
        # seconds = int(round(((decTime-hours)*60-minutes)*60))
        # time = [hours, minutes, seconds]

        
        if not dose:
            return totalDistance, time, maxDose, avgDose, totalDose

        estimate = array('d', [])
        
        # total dose, distance
        i = 0
        for rate in dose:
            
            if self.abort == True:
                break

            if len(dose) == 0:
                return

            if (i+1) < len(dose):
                point1 = [vertexX[index[i]], vertexY[index[i]]]
                point2 = [vertexX[index[i+1]], vertexY[index[i+1]]]

            elif (i+1) == len(dose):
                point1 = [vertexX[index[i-1]], vertexY[index[i-1]]]
                point2 = [vertexX[index[i]], vertexY[index[i]]]

            dist = self.distance(point1,point2)
            interval = (dist/1000)/float(self.speed)
            estimate.append(interval * rate)
            
            totalDistance = totalDistance + dist
            
            i = i + 1
            self.computeStat.emit(float(i)/len(dose) * 100, u'(3/4) Computing and creating report file...')
        
        # max dose
        maxDose = max(dose)

        # avg dose
        avgDose = sum(dose)/float(len(dose))
        
        if str(self.units) == 'nanoSv/h':
            totalDose = sum(estimate)
            
        elif str(self.units) == 'microSv/h':
            totalDose = sum(estimate) * 1000
            avgDose = avgDose * 1000
            maxDose = maxDose * 1000
            
        elif str(self.units) == 'nanoGy/h':
            totalDose = coef * sum(estimate)
            avgDose = coef * avgDose
            maxDose = coef * maxDose
            
        elif str(self.units) == 'microGy/h':
            totalDose = coef * sum(estimate) * 1000
            avgDose = coef * avgDose * 1000
            maxDose = coef * maxDose * 1000
            
        totalDose = round(totalDose,6)
        avgDose = round(avgDose,6)
        maxDose = round(maxDose,6)
        totalDistance = round(totalDistance/1000,3)
        
        # total time
        decTime = totalDistance/float(self.speed)
        hours = int(decTime)
        minutes = int((decTime-hours)*60)
        seconds = int(round(((decTime-hours)*60-minutes)*60))
        time = [hours, minutes, seconds]
        
        return totalDistance, time, maxDose, avgDose, totalDose

    def createReport(self, trackName, time, distance, maxDose, avgDose, totalDose):
        """Create report file.
        
        Prints error when report txt file cannot be opened for writing.

        :trackLayer: name of track layer
        :time: monitoring time
        :distance: length of route
        :maxDose: maximal dose rate on route
        :avgDose: average dose rate on route
        :totalDose: total dose rate on route
        """
        self.speed = float(self.speed.replace(',', '.'))

        try:
            try:
                # python 3.x
                report = open(self.reportFileName, 'w', encoding='utf-8')
            except:
                # python 2.x
                report = codecs.open(self.reportFileName, 'w', encoding='utf-8')
        except IOError as e:
            self.computeMessage.emit(u'Error', u'Unable open {} for writing. Reason: {}'.format(self.reportFileName, e),'CRITICAL')
            return

        report.write(u'QGIS ground radiation monitoring plugin report{ls}{ls}'.format(ls = os.linesep))
        report.write(u'Route information{ls}'.format(ls = os.linesep))
        report.write(u'--------------------------------------{ls}'.format(ls = os.linesep))
        report.write(u'route: {trackName}{ls}'.format(trackName = trackName, 
                                                      ls = os.linesep))
        report.write(u'monitoring speed (km/h): {speed}{ls}'.format(speed = self.speed, 
                                                                    ls = os.linesep))
        report.write(u'total monitoring time: {hours}:{minutes}:{seconds}{ls}'.format(hours = time[0],
                                                                                  minutes = time[1],
                                                                                  seconds = time[2],
                                                                                  ls = os.linesep))
        report.write(u'total distance (km): {distance}{ls}{ls}'.format(distance = distance,
                                                                   ls = os.linesep))
        report.write(u'Radiation values (estimated){ls}'.format(ls = os.linesep))
        report.write(u'--------------------------------------{ls}'.format(ls = os.linesep))
        report.write(u'maximum dose rate (nSv/h): {maxDose}{ls}'.format(maxDose = maxDose,
                                                                        ls = os.linesep))
        report.write(u'average dose rate (nSv/h): {avgDose}{ls}'.format(avgDose = avgDose,
                                                                        ls = os.linesep))
        report.write(u'total dose (nSv): {totalDose}'.format(totalDose = totalDose))

        report.close()

    def createShp(self, vertexX, vertexY, trackLayer):
        """Create ESRI shapefile and write new points. 

        :vertexX: X coordinates of points
        :vertexY: Y coordinates of points
        :trackLayer: layer to get coordinate system from
        """

        reader = csv.DictReader(open(self.tr(u'{f}').format(f = self.csvFileName),"rb"),
                                delimiter=',',
                                quoting=csv.QUOTE_NONE)

        # set up the shapefile driver
        driver = ogr.GetDriverByName("ESRI Shapefile")
        
        # create the data source
        dataSource = driver.CreateDataSource(self.tr(u'{f}').format(f=self.shpFileName))

        # create the spatial reference
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(trackLayer.crs().authid()[5:]))

        # create the layer
        layer = dataSource.CreateLayer('{}'.format(self.shpFileName.encode('utf8')), srs, ogr.wkbPoint)

        # Add the fields we're interested in
        layer.CreateField(ogr.FieldDefn("X", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("Y", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("dose rate", ogr.OFTString))

        # Process the text file and add the attributes and features to the shapefile
        i = 0
        rows = len(vertexX)
        for row in reader:
            
            if self.abort == True:
                    break
            
            i = i + 1
            self.computeStat.emit(float(i)/rows * 100, u'(4/4) Creating shape file...')
            # create the feature
            feature = ogr.Feature(layer.GetLayerDefn())
            # Set the attributes using the values from the delimited text file
            feature.SetField("X", row["X"])
            feature.SetField("Y", row["Y"])
            feature.SetField("dose rate", row["dosage"])

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
        dataSource = None