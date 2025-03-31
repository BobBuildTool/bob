# Bob build tool
# Copyright (C) 2017 Ralf Hubert
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .. import BOB_VERSION
from ..input import RecipeSet
from ..tty import colorize
from ..utils import runInEventLoop
from .helpers import processDefines
import argparse
import asyncio
import json
import re
import os
import collections
from shutil import copyfile

def findPackages(package, excludes, highlights, done, maxdepth, donePackages, level = 0):
    def isHighLigh(package):
        for h in highlights:
            if (h.search(package.getName())):
                return True
        return False

    # used for coloring packages in the graph
    # id: 1 - root packages / packages queried for
    #     2 - packages matching the 'highlight' argument
    #     3 - all other packages
    def getColorId(package, level):
        if package.getRecipe().isRoot():
            return 1
        elif level == 0:
            return 1
        elif isHighLigh(package):
            return 2
        return 3

    if package._getId() in donePackages:
       return
    donePackages.add(package._getId())

    for e in excludes:
        if (e.search(package.getName())):
            return

    if package.getPackageStep().isValid():
        if package._getId() not in done:
            done.add(package._getId())
            yield ('node', package, getColorId(package, level))

    if (maxdepth != -1) and (level >= maxdepth):
        return

    for d in package.getDirectDepSteps():
        for e in excludes:
            if (e.search(d.getPackage().getName())):
                break
        else:
           yield ('link', d.getPackage(), package)
        yield from findPackages(d.getPackage(), excludes, highlights, done, maxdepth, donePackages, level+1)

def makeD3Graph(recipes, packages, p, filename, options, excludes, highlights, maxdepth):
    def getHover(package, options):
        hover = collections.OrderedDict(package.getMetaEnv())
        if options.get('d3.showScm', False) and package.getCheckoutStep().isValid():
            scmidx = 0
            for s in package.getCheckoutStep().getScmList():
                p = s.getProperties(False)
                hover['scm'+str(scmidx)] = p.get('scm')
                hover['url'+str(scmidx)] = p.get('url')
                scmidx = scmidx + 1
        return hover

    nodes = []
    links = []
    done = set()
    donePackages = set()
    for package in packages.queryPackagePath(p):
        for (t, a, b) in findPackages(package, excludes, highlights, done, maxdepth, donePackages):
            if t == 'node':
                # do we already have this package? if so add a suffix
                maxVariant = -1
                for i, n in enumerate(nodes):
                    split = n['label'].split('#')
                    if len(split) == 2 and split[0] == a.getName():
                        if int(split[1]) > maxVariant: maxVariant = int(split[1])
                    elif split[0] == a.getName():
                        n['label'] = split[0]+'#0'
                        nodes[i] = n
                nodes.append({"id" : a._getId(),
                    "label" : a.getName() + (('#'+str(maxVariant+1)) if maxVariant != -1 else ''),
                    "colorid" : b,
                    "hover" : json.dumps(getHover(a, options))})
            elif t == 'link':
                link = {"target": a._getId(), "source" : b._getId()}
                if link not in links:
                    links.append(link)

    localLib = options.get('d3.localLib', None)
    if localLib:
        try:
            copyfile(localLib, os.path.join(os.path.dirname(filename),os.path.basename(localLib)))
        except FileNotFoundError as e:
            print(colorize("WARNING: local D3 lib not found - using upstream link ({})".format(e), "33"))
            localLib = None

    with open(filename + ".html", "w") as f:
        f.write(
"""<!DOCTYPE html>
<head>
<title>Bob graph: """ + p + """ </title>
<style>
 .info { font-family: arial; font-size: 10pt;}
</style
</head>
<meta charset="utf-8">
<svg width="0" height="0"></svg>
Highlight packages depending on the selected: <input type="checkbox" id="highlightAll">
<script src='""")
        f.write(os.path.basename(localLib) if localLib else "https://d3js.org/d3.v4.min.js")
        f.write(
"""'></script>
<script>
var nodes =
""")
        json.dump(nodes, f)
        f.write("\nvar links =")
        json.dump(links, f)
        f.write("""\n

function getDependencies(node) {
  return links.reduce(function (neighbors, link) {
      if (link.source.id === node.id) {
        neighbors.push(link.target.id)
      }
      return neighbors
    },
    [node.id]
  )
}

function isDependency(node, link) {
  if ( document.getElementById("highlightAll").checked == true) {
    return (link.source.id === node.id || link.target.id === node.id )
  } else {
    return link.source.id === node.id
  }
}

function getNodeColor(node, neighbors) {
  if (Array.isArray(neighbors) && neighbors.indexOf(node.id) > -1) {
   switch(node.colorid) {
    case 1:
        return 'blue';
    case 2:
        return 'pink';
    default:
        return 'green';
   }
  }

  switch(node.colorid) {
    case 1:
        return 'red';
    case 2:
        return 'orange';
    default:
        return 'gray';
  }
}

function getArrow(node, link) {
   if (( document.getElementById("highlightAll").checked == true) && ( link.target.id === node.id )) {
       return 'url(#arrowhead_dark_magenta)';
   } else if (link.source.id === node.id ) {
       return 'url(#arrowhead_green)';
   } else {
       return 'url(#arrowhead_gray)';
   }
}

function getLinkColor(node, link) {
   if (( document.getElementById("highlightAll").checked == true) && ( link.target.id === node.id )) {
       return '#8B008B';
   } else if (link.source.id === node.id ) {
       return 'green';
   } else {
       return '#E5E5E5'
   }
}


var width = window.innerWidth
var height = window.innerHeight

var zoom = d3.zoom()
    .scaleExtent([0.01, 10])
    .on("zoom", zoomed);

var svg = d3.select('svg')
var container = svg.append("g")

svg.call(zoom)

window.addEventListener('resize', function(event){
    // resize the svg if the window size changes
    var width = window.innerWidth
    var height = window.innerHeight > 200 ? window.innerHeight - 80 : window.innerHeight
    svg.attr('width', width).attr('height', height)
});

// simulation setup with all forces
var linkForce = d3
  .forceLink()
  .id(function (link) { return link.id }).distance(80).strength(0.5)

var attractForce = d3.forceManyBody().strength(1).distanceMax(800)
                     .distanceMin(80);

var collisionForce = d3.forceCollide(80).strength(0.5).iterations(20);

var simulation = d3
  .forceSimulation()
  .nodes(nodes)
  .force('collide', collisionForce)
  .force('link', linkForce)
  .force("center", d3.forceCenter(width / 2, height / 2))

function updateNodes() {
  var neighbors = getDependencies(lastSelected)

  // we modify the styles to highlight selected nodes
  nodeElements.attr('fill', function (node) { return getNodeColor(node, neighbors) })
  linkElements.attr('stroke', function (link) { return getLinkColor(lastSelected, link) })
  linkElements.attr('marker-end', function (link) { return getArrow(lastSelected, link) })
}

function selectNode(selectedNode) {
  lastSelected = selectedNode
  updateNodes();
}

// Create Event Handlers for mouse
function handleMouseOver(d, i) {
  var meta = JSON.parse(d.hover);
  var entries = Object.keys(meta).length

  if (entries != 0) {
    // find the longest entry
    var max = 0
    var lineHeight = 12
    for (e in meta) {
        if (e.length + meta[e].length > max) {
            max = e.length + meta[e].length
        }
    }

    // draw a box
    container.append("rect")
        .attr('id', "tb" + d.id)
        .attr("x", d.x + this.getComputedTextLength())
        .attr("y", d.y - 13)
        .attr("width",  (max+2) * 6 + 20 )
        .attr("height", entries * lineHeight + 10 )
        .attr("rx", 5)
        .attr("ry", 5)
        .attr("style", "fill:white;stroke:black;stroke-width:1")

    var line = 0
    for (e in meta) {
        // add the text
        container
            .append("text")
            .attr('id', "t" + d.id)  // Create an id for text so we can select it later for removing on mouseout
            .attr('x', d.x + this.getComputedTextLength() + 10)
            .attr('y', d.y + line*lineHeight)
            .text(e +': ' + meta[e])  // Value of the text
                .attr("font-size", 10)
                .attr("font-family", "Courier New")
        line += 1
    }
  }
}

function handleMouseOut(d, i) {
    d3.selectAll("#t" + d.id).remove();  // Remove text location
    d3.select("#tb" + d.id).remove();  // Remove textbox location
}

function zoomed() {
  container.attr("transform", d3.event.transform);
}

// Arrow marker for end-of-line arrow
svg.append('defs').append('marker')
    .attr('id', 'arrowhead_gray')
    .attr('refX', 100)
    .attr('refY',   5)
    .attr('markerWidth', 15)
    .attr('markerHeight',15)
    .attr('orient', 'auto')
    .attr('fill', '#E5E5E5')
    .append('path')
    .attr('d', 'M 0,0 l 10,5 l -10,5 Z');

svg.append('defs').append('marker')
    .attr('id', 'arrowhead_green')
    .attr('refX', 100)
    .attr('refY',  5)
    .attr('markerWidth', 15)
    .attr('markerHeight',15 )
    .attr('orient', 'auto')
    .attr('fill', 'green')
    .append('path')
    .attr('d', 'M 0,0 l 10,5 l -10,5 Z');

svg.append('defs').append('marker')
    .attr('id', 'arrowhead_dark_magenta')
    .attr('refX', 100)
    .attr('refY',  5)
    .attr('markerWidth', 15)
    .attr('markerHeight',15 )
    .attr('orient', 'auto')
    .attr('fill', '#8B008B')
    .append('path')
    .attr('d', 'M 0,0 l 10,5 l -10,5 Z');

var linkElements = container.attr("class", "links")
  .selectAll("line")
  .data(links)
  .enter().append("line")
    .attr("stroke-width", 1)
    .attr("stroke", "rgba(50, 50, 50, 0.2)")
    .attr("marker-end", 'url(#arrowhead_gray)');

var nodeElements = container.attr("class", "nodes")
  .selectAll("rect")
  .data(nodes)
  .enter().append("rect")
  .attr("width", function (node) {
          if (node.label.length >= 20) {
             return 130
          } else {
             return node.label.length * 6 + 10
         }})
  .attr("height", function (node) {
          if (node.label.length >= 20) {
             return Math.floor((node.label.length+19) / 20) * 14
          } else {
             return 14
          }})
  .attr("rx", 2)
  .attr("ry", 2)
    .attr("fill", getNodeColor)
  .on('click', selectNode)""")

        if options.get('d3.dragNodes', False):
            f.write("""
  .call(d3.drag()
     .on("start", dragstarted)
     .on("drag", dragged)
     .on("end", dragended));

function dragstarted(d) {
  if (!d3.event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x;
  d.fy = d.y;
}

function dragged(d) {
  d.fx = d3.event.x;
  d.fy = d3.event.y;
}

function dragended(d) {
  if (!d3.event.active) simulation.alphaTarget(0);
  d.fx = null;
  d.fy = null;
}""")
        f.write("""\n
var textElements = container.attr("class", "texts")
  .selectAll("text")
  .data(nodes)
  .enter().append("text")
    .text(function (node) { return  node.label })
    .attr("font-size", 10)
    .attr("font-family", "Courier New")
    .attr("dx", 5)
    .attr("dy", 10)
    .on('click', selectNode)
    .on("mouseover", handleMouseOver)
    .on("mouseout", handleMouseOut)
    .call(wrap)""")
        if options.get('d3.dragNodes', False):
            f.write("""
    .call(d3.drag()
     .on("start", dragstarted)
     .on("drag", dragged)
     .on("end", dragended));
""")
        f.write("""\n
function wrap(text) {
   text.each(function() {
        var text = d3.select(this),
        words = text.text().match(/.{1,20}/g).reverse(),
        word,
        line = [],
        lineNumber = 0,
        lineHeight = 1.2,
        x = text.attr("x"),
        y = text.attr("y"),
        dy = text.attr("dy") ? text.attr("dy") : 0;
        tspan = text.text(null).append("tspan");
        while (word = words.pop()) {
            line.push(word);
            tspan.text(line.join(" "));
            if (tspan.node().getComputedTextLength() > 125) {
                line.pop();
                tspan.text(line.join(" "));
                line = [word];
                tspan = text.append("tspan").attr("dx", -120).attr("dy", lineHeight + dy + "em").text(word);
            }
        }
    });
}

var busy = document.createElement('div');
busy.setAttribute("id","busy")
busy.innerHTML = "<p style='font-size:20pt'> Please wait...</p>"
document.body.appendChild(busy);
d3.select("#highlightAll").on("change",updateNodes);
simulation.nodes(nodes).on('end', ticked)
function ticked() {
  busy = document.getElementById("busy")
  if (busy) {
    simulation.nodes(nodes).on('tick', ticked)
    busy.remove();
    // do not make the svg as heigh as the window to see the footer
    svg.attr('width', width).attr('height', height > 200 ? height - 80 : height)
  }
  nodeElements
    .attr('x', function (node) { return node.x })
    .attr('y', function (node) { return node.y })
  textElements
    .attr('x', function (node) { return node.x })
    .attr('y', function (node) { return node.y })
  linkElements
    .attr('x1', function (link) {
        if (link.source.label.length > 21) {
            return link.source.x + 65
        } else {
            return link.source.x + link.source.label.length * 3
        }
    })
    .attr('y1', function (link) {
            return link.source.y + ((link.source.label.length + 20) / 21-1) * 12
        })
    .attr('x2', function (link) {
        if (link.target.label.length > 21) {
            return link.target.x + 65
        } else {
            return link.target.x + link.target.label.length * 3 }
        })
    .attr('y2', function (link) {
            return link.target.y + ((link.target.label.length + 20)/21-1) * 12
        })
}

simulation.force("link").links(links)
</script>
<div class='info' style="width: 100%; text-align: center;">
  <div id='innerLeft' style="float: left"> Recipes: """ + runInEventLoop(recipes.getScmStatus()) + """</div>
  <div id='innerRight' style="float: right">Bob version: """ + BOB_VERSION +"""</div>
  <div id='innerMiddle' style="display: inline-block">Generated using <a href="https://www.d3js.org">D3JS</a></div>
</div>
""")

def makeDotGraph(packages, p, filename, excludes, highlights, maxdepth):
    def getDotColor(c):
        if c == 1:
            return 'red';
        elif c == 2:
            return 'orange';
        else:
            return 'white';

    done = set()
    donePackages = set()
    links = []
    with open(filename + ".dot", "w") as f:
        f.write("digraph \"" + p + "\" {\n")
        f.write(" {\n")
        for package in packages.queryPackagePath(p):
            for (t, a, b) in findPackages(package, excludes, highlights, done, maxdepth, donePackages):
                if t == 'link':
                    if (a.getName(),b.getName()) not in links:
                        links.append((a.getName(), b.getName()))
                elif t == 'node':
                    f.write('   "' + a.getName() + '" [fillcolor=' + getDotColor(b) + ',style=filled]\n')
        f.write(" }\n")
        for (s,d) in links:
            f.write('"{}" -> "{}";\n'.format(d, s))
        f.write("}\n")

def doGraph(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob graph", description='Generate dependency graph')
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="Graph entry (sub-)package")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=False,
        help="Enable sandboxing")
    group.add_argument('--slim-sandbox', action='store_false', dest='sandbox',
        help="Enable slim sandboxing")
    group.add_argument('--dev-sandbox', action='store_true', dest='sandbox',
        help="Enable development sandboxing")
    group.add_argument('--strict-sandbox', action='store_true', dest='sandbox',
        help="Enable strict sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of graph files.")
    parser.add_argument('-e', '--exclude', default=[], action='append', dest="excludes",
        help="Do not show packages matching this regex. (And all it's deps)")
    parser.add_argument('-f', '--filename', default=None,
        help="Name of Outputfile.")
    parser.add_argument('-H', '--highlight', default=[], action='append', dest="highlights",
        help="Highlight packages matching this regex.")
    parser.add_argument('-n', '--max-depth', type=int, default=None, help="Max depth.")
    parser.add_argument('-t', '--type', choices=['d3', 'dot'], default=None,
        help="Graph type. d3 (default) or dot")
    parser.add_argument("-o", default=[], action='append', dest='options',
        help="Set extended options")
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)
    packages = recipes.generatePackages(lambda s,m: "unused", args.sandbox)

    cfg = recipes.getCommandConfig().get('graph', {})
    defaults = {
        'type' : 'd3',
        'max_depth' : -1,
    }

    options = cfg.get('options', {})
    for i in args.options:
        (opt, sep, val) = i.partition("=")
        if sep != "=":
            parser.error("Malformed plugin option: "+i)
        if val != "":
            options[opt] = val

    for a in vars(args):
       if a=='options': continue
       if getattr(args, a) == None:
            setattr(args, a, cfg.get(a, defaults.get(a)))

    excludes = []
    if args.excludes:
        for e in args.excludes:
            excludes.append(re.compile(e))

    highlights = []
    if args.highlights:
        for e in args.highlights:
            highlights.append(re.compile(e))

    destination = os.path.join(os.getcwd(), 'graph') if not args.destination else args.destination
    if not os.path.exists(destination):
        os.makedirs(destination)

    for p in args.packages:
        # convert the package-query into a valid file name
        filename = args.filename if args.filename else "".join([c for c in p if c.isalpha() or c.isdigit() or c==' ']).rstrip()

        print(">>", colorize(p, "32;1"))
        print(colorize("   GRAPH   {} ({})".format(p, args.type), "32"))

        if args.type == 'd3':
            makeD3Graph(recipes, packages, p, os.path.join(destination, filename), options, excludes, highlights, args.max_depth)
        elif args.type == 'dot':
            makeDotGraph(packages, p, os.path.join(destination, filename), excludes, highlights, args.max_depth)
