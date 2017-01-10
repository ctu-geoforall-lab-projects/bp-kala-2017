# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GroundRadiationMonitoring
                                 A QGIS plugin
 This plugin calculates the amount of received radiation. 
                             -------------------
        begin                : 2017-01-10
        copyright            : (C) 2017 by Michael Kala
        email                : michael.kala@email.cz
        git sha              : $Format:%H$
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
 This script initializes the plugin, making it known to QGIS.
"""


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load GroundRadiationMonitoring class from file GroundRadiationMonitoring.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    #
    from .ground_radiation_monitoring import GroundRadiationMonitoring
    return GroundRadiationMonitoring(iface)
