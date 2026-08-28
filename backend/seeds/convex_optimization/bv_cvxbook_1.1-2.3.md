Convex Optimization

## Chapter 1

## Introduction

In this introduction we give an overview of mathematical optimization, focusing on the special role
of convex optimization. The concepts introduced informally here will be covered in later chapters,
with more care and technical detail.

## 1.1 Mathematical optimization

A mathematical optimization problem, or just optimization problem, has the form

$$
{ \begin{array} { l l } { { \mathrm { minimize } } } & { f _ { 0 } ( x ) } \\ { { \mathrm { subject to } } } & { f _ { i } ( x ) \leq b _ { i } , \quad i = 1 , \ldots , m . } \end{array} }\tag{1.1}
$$

Here the vector $x = ( x _ { 1 } , \ldots , x _ { n } ) $ is the optimization variable of the
problem, the function $f _ { 0 } : \mathbf { R } ^ { n } \to \mathbf { R }$ is the objective
function, the functions $f_{ i } : \mathbf { R } ^ { n } \to \mathbf { R }$ $i = 1 , \ldots , m$ ,
are the (inequality) constraint functions, and the constants $b_{ 1 } , \dots , b_{ m }$ are the
limits, or bounds, for the constraints. A vector $x ^ { \star }$ is called optimal, or a solution of
the problem (1.1), if it has the smallest objective value among all vectors that satisfy the
constraints: for any z with $f _ { 1 } ( z ) \leq b _ { 1 } , \ldots , f _ { m } ( z ) \leq b _ { m }$ ,
we have $f _ { 0 } ( z ) \geq f _ { 0 } ( x ^ { \star } )$

We generally consider families or classes of optimization problems, characterized by particular
forms of the objective and constraint functions. As an important example, the optimization problem
(1.1) is called a linear program if the objective and constraint functions
$f _ { 0 } , \ldots , f _ { m }$ are linear, i.e., satisfy

$$
f _ { i } ( \alpha x + \beta y ) = \alpha f _ { i } ( x ) + \beta f _ { i } ( y )\tag{1.2}
$$

for all $x ,  y \in \mathbf { R } ^ { n }$ and all $\alpha , \beta \in \mathbf { R }$ . If the
optimization problem is not linear, it is called a nonlinear program.

This book is about a class of optimization problems called convex optimization problems. A convex
optimization problem is one in which the objective and constraint functions are convex, which means
they satisfy the inequality

$$
f _ { i } ( \alpha x + \beta y ) \leq \alpha f _ { i } ( x ) + \beta f _ { i } ( y )\tag{1.3}
$$

for all $x , y \in \mathbf { R } ^ { n }$ and all $\alpha ,  \beta \in \mathbf { R }$ with
$\alpha + \beta = 1 , \alpha \geq 0 , \beta \geq 0$ . Comparing (1.3) and (1.2), we see that
convexity is more general than linearity: inequality replaces the more restrictive equality, and the
inequality must hold only for certain values of $\alpha$ and $ \beta $ Since any linear program is
therefore a convex optimization problem, we can consider convex optimization to be a generalization
of linear programming.

## 1.1.1 Applications

The optimization problem (1.1) is an abstraction of the problem of making the best possible choice
of a vector in $\mathbf { R } ^ { n }$ from a set of candidate choices. The variable x represents
the choice made; the constraints $f _ { i } ( x ) \leq b _ { i }$ represent firm requirements or
specifications that limit the possible choices, and the objective value $f _ { 0 } ( x )$ represents
the cost of choosing x. (We can also think of $- f _ { 0 } ( x )$ as representing the value, or
utility, of choosing x.) A solution of the optimization problem (1.1) corresponds to a choice that
has minimum cost (or maximum utility), among all choices that meet the firm requirements.

In portfolio optimization, for example, we seek the best way to invest some capital in a set of n
assets. The variable $x _ { i }$ represents the investment in the ith asset, so the vector
$x \in \mathbf { R } ^ { n }$ describes the overall portfolio allocation across the set of assets.
The constraints might represent a limit on the budget (i.e., a limit on the total amount to be
invested), the requirement that investments are nonnegative (assuming short positions are not
allowed), and a minimum acceptable value of expected return for the whole portfolio. The objective
or cost function might be a measure of the overall risk or variance of the portfolio return. In this
case, the optimization problem (1.1) corresponds to choosing a portfolio allocation that minimizes
risk, among all possible allocations that meet the firm requirements.

Another example is device sizing in electronic design, which is the task of choosing the width and
length of each device in an electronic circuit. Here the variables represent the widths and lengths
of the devices. The constraints represent a variety of engineering requirements, such as limits on
the device sizes imposed by the manufacturing process, timing requirements that ensure that the
circuit can operate reliably at a specified speed, and a limit on the total area of the circuit. A
common objective in a device sizing problem is the total power consumed by the circuit. The
optimization problem (1.1) is to find the device sizes that satisfy the design requirements (on
manufacturability, timing, and area) and are most power eficient.

In data fitting, the task is to find a model, from a family of potential models, that best fits some
observed data and prior information. Here the variables are the parameters in the model, and the
constraints can represent prior information or required limits on the parameters (such as
nonnegativity). The objective function might be a measure of misfit or prediction error between the
observed data and the values predicted by the model, or a statistical measure of the unlikeliness or
implausibility of the parameter values. The optimization problem (1.1) is to find the model
parameter values that are consistent with the prior information, and give the smallest misfit or
prediction error with the observed data (or, in a statistical framework, are most likely).

An amazing variety of practical problems involving decision making (or system design, analysis, and
operation) can be cast in the form of a mathematical optimization problem, or some variation such as
a multicriterion optimization problem. Indeed, mathematical optimization has become an important
tool in many areas. It is widely used in engineering, in electronic design automation, automatic
control systems, and optimal design problems arising in civil, chemical, mechanical, and aerospace
engineering. Optimization is used for problems arising in network design and operation, finance,
supply chain management, scheduling, and many other areas. The list of applications is still
steadily expanding.

For most of these applications, mathematical optimization is used as an aid to a human decision
maker, system designer, or system operator, who supervises the process, checks the results, and
modifies the problem (or the solution approach) when necessary. This human decision maker also
carries out any actions suggested by the optimization problem, e.g., buying or selling assets to
achieve the optimal portfolio.

A relatively recent phenomenon opens the possibility of many other applications for mathematical
optimization. With the proliferation of computers embedded in products, we have seen a rapid growth
in embedded optimization. In these embedded applications, optimization is used to automatically make
real-time choices, and even carry out the associated actions, with no (or little) human intervention
or oversight. In some application areas, this blending of traditional automatic control systems and
embedded optimization is well under way; in others, it is just starting. Embedded real-time
optimization raises some new challenges: in particular, it requires solution methods that are
extremely reliable, and solve problems in a predictable amount of time (and memory).

## 1.1.2 Solving optimization problems

A solution method for a class of optimization problems is an algorithm that computes a solution of
the problem (to some given accuracy), given a particular problem from the class, i.e., an instance
of the problem. Since the late 1940s, a large efort has gone into developing algorithms for solving
various classes of optimization problems, analyzing their properties, and developing good software
implementations. The efectiveness of these algorithms, i.e., our ability to solve the optimization
problem (1.1), varies considerably, and depends on factors such as the particular forms of the
objective and constraint functions, how many variables and constraints there are, and special
structure, such as sparsity. (A problem is sparse if each constraint function depends on only a
small number of the variables).

Even when the objective and constraint functions are smooth (for example, polynomials) the general
optimization problem (1.1) is surprisingly dificult to solve. Approaches to the general problem
therefore involve some kind of compromise, such as very long computation time, or the possibility of
not finding the solution. Some of these methods are discussed in 1.4.

There are, however, some important exceptions to the general rule that most optimization problems
are dificult to solve. For a few problem classes we have efective algorithms that can reliably solve
even large problems, with hundreds or thousands of variables and constraints. Two important and well
known examples, described in 1.2 below (and in detail in chapter 4), are least-squares problems and
linear programs. It is less well known that convex optimization is another exception to the rule:
Like least-squares or linear programming, there are very efective algorithms that can reliably and
eficiently solve even large convex problems.

## 1.2 Least-squares and linear programming

In this section we describe two very widely known and used special subclasses of convex
optimization: least-squares and linear programming. (A complete technical treatment of these
problems will be given in chapter 4.)

## 1.2.1 Least-squares problems

A least-squares problem is an optimization problem with no constraints ( i . e . ,$ m = 0$) and an
objective which is a sum of squares of terms of the form $a _ { i } ^ { T } x - b _ { i }$

$$
\begin{array} { r l } { \mathrm {minimize} } & { { } f _ { 0 } ( x ) = \| A x - b \| _ { 2 } ^ { 2 } = \sum _ { i = 1 } ^ { k } ( a _ { i } ^ { T } x - b _ { i } ) ^ { 2 } . } \end{array}\tag{1.4}
$$

Here $A \in \mathbf { R } ^ { k \times n } $ ( with $k \geq n$ ) , $a _ { i } ^ { T }$ are the rows
of A, and the vector $ x \in \mathbf { R } ^ { n }$ is the optimization variable.

## Solving least-squares problems

The solution of a least-squares problem (1.4) can be reduced to solving a set of linear equations,

$$
( A ^ { T } A ) x = A ^ { T } b ,
$$

so we have the analytical solution $x = ( A ^ { T } A ) ^ { - 1 } A ^ { T } b$ . For least-squares
problems we have good algorithms (and software implementations) for solving the problem to high
accuracy, with very high reliability. The least-squares problem can be solved in a time
approximately proportional to $n ^ { 2 } k$ , with a known constant. A current desktop computer can
solve a least-squares problem with hundreds of variables, and thousands of terms, in a few seconds;
more powerful computers, of course, can solve larger problems, or the same size problems, faster.
(Moreover, these solution times will decrease exponentially in the future, according to Moore’s
law.) Algorithms and software for solving least-squares problems are reliable enough for embedded
optimization.

In many cases we can solve even larger least-squares problems, by exploiting some special structure
in the coeficient matrix A. Suppose, for example, that the matrix A is sparse, which means that it
has far fewer than kn nonzero entries. By exploiting sparsity, we can usually solve the
least-squares problem much faster than order $n ^ { 2 } k$ . A current desktop computer can solve a
sparse least-squares problem with tens of thousands of variables, and hundreds of thousands of
terms, in around a minute (although this depends on the particular sparsity pattern).

For extremely large problems (say, with millions of variables), or for problems with exacting
real-time computing requirements, solving a least-squares problem can be a challenge. But in the
vast majority of cases, we can say that existing methods are very efective, and extremely reliable.
Indeed, we can say that solving least-squares problems (that are not on the boundary of what is
currently achievable) is a (mature) technology, that can be reliably used by many people who do not
know, and do not need to know, the details.

## Using least-squares

The least-squares problem is the basis for regression analysis, optimal control, and many parameter
estimation and data fitting methods. It has a number of statistical interpretations, $e . g .$ , as
maximum likelihood estimation of a vector $x ,$ given linear measurements corrupted by Gaussian
measurement errors.

Recognizing an optimization problem as a least-squares problem is straightforward; we only need to
verify that the objective is a quadratic function (and then test whether the associated quadratic
form is positive semidefinite). While the basic least-squares problem has a simple fixed form,
several standard techniques are used to increase its flexibility in applications.

In weighted least-squares, the weighted least-squares cost

$$
\sum _ { i = 1 } ^ { k } w _ { i } ( a _ { i } ^ { T } x - b _ { i } ) ^ { 2 } ,
$$

where $w _ { 1 } , \ldots , w _ { k }$ are positive, is minimized. (This problem is readily cast and
solved as a standard least-squares problem.) Here the weights w<sub>i</sub> are chosen to reflect
difering levels of concern about the sizes of the terms $a _ { i } ^ { T } x - b _ { i }$ , or
simply to influence the solution. In a statistical setting, weighted least-squares arises in
estimation of a vector x, given linear measurements corrupted by errors with unequal variances.

Another technique in least-squares is regularization, in which extra terms are added to the cost
function. In the simplest case, a positive multiple of the sum of squares of the variables is added
to the cost function:

$$
\sum _ { i = 1 } ^ { k } ( a _ { i } ^ { T } x - b _ { i } ) ^ { 2 } + \rho \sum _ { i = 1 } ^ { n } x _ { i } ^ { 2 } ,
$$

where $\rho > 0$ . (This problem too can be formulated as a standard least-squares problem.) The
extra terms penalize large values of $x ,$ and result in a sensible solution in cases when
minimizing the first sum only does not. The parameter $\rho$ is chosen by the user to give the right
trade-of between making the original objective function
$\sum _ { i = 1 } ^ { k } ( a _ { i } ^ { T } x -  b _ { i } ) ^ { 2 }$ small, while keeping
$\sum _ { i = 1 } ^ { n } x _ { i } ^ { 2 }$ not too big. Regularization comes up in statistical
estimation when the vector x to be estimated is given a prior distribution.

Weighted least-squares and regularization are covered in chapter $6 ;$ their statistical
interpretations are given in chapter 7.

## 1.2.2 Linear programming

Another important class of optimization problems is linear programming, in which the objective and
all constraint functions are linear:

$$
\begin{array} { l l } { \mathrm {minimize} } & { c ^ { T } x } \\ { \mathrm {subject to} } & { a _ { i } ^ { T } x \leq b _ { i } , \quad i = 1 , \dots , m . } \end{array}\tag{1.5}
$$

Here the vectors $c , a _ { 1 } , \ldots , a _ { m } \in \mathbf { R } ^ { n }$ and scalars
$b _ { 1 } , \dotsc , b _ { m } \in \mathbf { R }$ are problem parameters that specify the objective
and constraint functions.

## Solving linear programs

There is no simple analytical formula for the solution of a linear program (as there is for a
least-squares problem), but there are a variety of very efective methods for solving them, including
Dantzig’s simplex method, and the more recent interiorpoint methods described later in this book.
While we cannot give the exact number of arithmetic operations required to solve a linear program
(as we can for leastsquares), we can establish rigorous bounds on the number of operations required
to solve a linear program, to a given accuracy, using an interior-point method. The complexity in
practice is order $n ^ { 2 } m$ (assuming $m \geq n$ ) but with a constant that is less well
characterized than for least-squares. These algorithms are quite reliable, although perhaps not
quite as reliable as methods for least-squares. We can easily solve problems with hundreds of
variables and thousands of constraints on a small desktop computer, in a matter of seconds. If the
problem is sparse, or has some other exploitable structure, we can often solve problems with tens or
hundreds of thousands of variables and constraints.

As with least-squares problems, it is still a challenge to solve extremely large linear programs, or
to solve linear programs with exacting real-time computing requirements. But, like least-squares, we
can say that solving (most) linear programs is a mature technology. Linear programming solvers can
be (and are) embedded in many tools and applications.

## Using linear programming

Some applications lead directly to linear programs in the form (1.5), or one of several other
standard forms. In many other cases the original optimization problem does not have a standard
linear program form, but can be transformed to an equivalent linear program (and then, of course,
solved) using techniques covered in detail in chapter 4.

As a simple example, consider the Chebyshev approximation problem:

$$
\mathrm {minimize} \quad \mathrm {max} _ { i = 1 , \dots , k } | a _ { i } ^ { T } x - b _ { i } | .\tag{1.6}
$$

Here $ x  \in \mathbf { R } ^ { n }$ is the variable, and
$a _ { 1 } , \dotsc , a _ { k } \in \mathbf { R } ^ { n } , b _ { 1 } , \dotsc , b _ { k } \in \mathbf { R }$
are parameters that specify the problem instance. Note the resemblance to the least-squares problem
(1.4). For both problems, the objective is a measure of the size of the terms
$a _ { i } ^ { T } x - b _ { i }$ . In least-squares, we use the sum of squares of the terms as
objective, whereas in Chebyshev approximation, we use the maximum of the absolute values.

One other important distinction is that the objective function in the Chebyshev approximation
problem (1.6) is not diferentiable; the objective in the least-squares problem (1.4) is quadratic,
and therefore diferentiable.

The Chebyshev approximation problem (1.6) can be solved by solving the linear program

$$
\begin{array} { r l } { \mathrm {minimize} } & { t } \\ { \mathrm {subject to} } & { a _ { i } ^ { T } x - t \leq b _ { i } , \quad i = 1 , \dots , k } \\ & { - a _ { i } ^ { T } x - t \leq - b _ { i } , \quad i = 1 , \dots , k , } \end{array}\tag{1.7}
$$

with variables $x \in \mathbf { R } ^ { n }$ and $t \in \textbf { R }$ . (The details will be given
in chapter $6 . )$ Since linear programs are readily solved, the Chebyshev approximation problem is
therefore readily solved.

Anyone with a working knowledge of linear programming would recognize the Chebyshev approximation
problem (1.6) as one that can be reduced to a linear program. For those without this background,
though, it might not be obvious that the Chebyshev approximation problem (1.6), with its
nondiferentiable objective, can be formulated and solved as a linear program.

While recognizing problems that can be reduced to linear programs is more involved than recognizing
a least-squares problem, it is a skill that is readily acquired, since only a few standard tricks
are used. The task can even be partially automated; some software systems for specifying and solving
optimization problems can automatically recognize (some) problems that can be reformulated as linear
programs.

## 1.3 Convex optimization

A convex optimization problem is one of the form

$$
\begin{array} { l l } { \mathrm {minimize} } & { f _ { 0 } ( x ) } \\ { \mathrm { subject to} } & { f _ { i } ( x ) \leq b _ { i } , \quad i = 1 , \dots , m , } \end{array}\tag{1.8}
$$

where the functions $f _ { 0 } , \ldots , f _ { m } : \mathbf { R } ^ { n } \to \mathbf { R }$ are
convex, i.e., satisfy

$$
f _ { i } ( \alpha x + \beta y ) \leq \alpha f _ { i } ( x ) + \beta f _ { i } ( y )
$$

for all $x , y \in \mathbf { R } ^ { n }$ and all $\alpha ,  \beta \in \mathbf { R }$ with
$\alpha + \beta = 1 , \alpha \geq 0 , \beta \geq 0$ . The least-squares problem (1.4) and linear
programming problem (1.5) are both special cases of the general convex optimization problem (1.8).

## 1.3.1 Solving convex optimization problems

There is in general no analytical formula for the solution of convex optimization problems, but (as
with linear programming problems) there are very efective methods for solving them. Interior-point
methods work very well in practice, and in some cases can be proved to solve the problem to a
specified accuracy with a number of operations that does not exceed a polynomial of the problem
dimensions. (This is covered in chapter 11.)

We will see that interior-point methods can solve the problem (1.8) in a number of steps or
iterations that is almost always in the range between 10 and 100. Ignoring any structure in the
problem (such as sparsity), each step requires on the order of

$$
\operatorname* {max} \{ n ^ { 3 } , n ^ { 2 } m , F \}
$$

operations, where F is the cost of evaluating the first and second derivatives of the objective and
constraint functions $f _ { 0 } , \ldots , f _ { m }$

Like methods for solving linear programs, these interior-point methods are quite reliable. We can
easily solve problems with hundreds of variables and thousands of constraints on a current desktop
computer, in at most a few tens of seconds. By exploiting problem structure (such as sparsity), we
can solve far larger problems, with many thousands of variables and constraints.

We cannot yet claim that solving general convex optimization problems is a mature technology, like
solving least-squares or linear programming problems. Research on interior-point methods for general
nonlinear convex optimization is still a very active research area, and no consensus has emerged yet
as to what the best method or methods are. But it is reasonable to expect that solving general con
vex optimization problems will become a technology within a few years. And for some subclasses of
convex optimization problems, for example second-order cone programming or geometric programming
(studied in detail in chapter 4), it is fair to say that interior-point methods are approaching a
technology.

## 1.3.2 Using convex optimization

Using convex optimization is, at least conceptually, very much like using least squares or linear
programming. If we can formulate a problem as a convex optimization problem, then we can solve it
eficiently, just as we can solve a least-squares problem eficiently. With only a bit of
exaggeration, we can say that, if you formulate a practical problem as a convex optimization
problem, then you have solved the original problem.

There are also some important diferences. Recognizing a least-squares problem is straightforward,
but recognizing a convex function can be dificult. In addition, there are many more tricks for
transforming convex problems than for transforming linear programs. Recognizing convex optimization
problems, or those that can be transformed to convex optimization problems, can therefore be
challenging. The main goal of this book is to give the reader the background needed to do this. Once
the skill of recognizing or formulating convex optimization problems is developed, you will find
that surprisingly many problems can be solved via convex optimization.

The challenge, and art, in using convex optimization is in recognizing and formulating the problem.
Once this formulation is done, solving the problem is, like least-squares or linear programming,
(almost) technology.

## 1.4 Nonlinear optimization

Nonlinear optimization (or nonlinear programming) is the term used to describe an optimization
problem when the objective or constraint functions are not linear, but not known to be convex.
Sadly, there are no efective methods for solving the general nonlinear programming problem (1.1).
Even simple looking problems with as few as ten variables can be extremely challenging, while
problems with a few hundreds of variables can be intractable. Methods for the general nonlinear
programming problem therefore take several diferent approaches, each of which involves some
compromise.

## 1.4.1 Local optimization

In local optimization, the compromise is to give up seeking the optimal x, which minimizes the
objective over all feasible points. Instead we seek a point that is only locally optimal, which
means that it minimizes the objective function among feasible points that are near it, but is not
guaranteed to have a lower objective value than all other feasible points. A large fraction of the
research on general nonlinear programming has focused on methods for local optimization, which as a
consequence are well developed.

Local optimization methods can be fast, can handle large-scale problems, and are widely applicable,
since they only require diferentiability of the objective and constraint functions. As a result,
local optimization methods are widely used in applications where there is value in finding a good
point, if not the very best. In an engineering design application, for example, local optimization
can be used to improve the performance of a design originally obtained by manual, or other, design
methods.

There are several disadvantages of local optimization methods, beyond (possibly) not finding the
true, globally optimal solution. The methods require an initial guess for the optimization variable.
This initial guess or starting point is critical, and can greatly afect the objective value of the
local solution obtained. Little information is provided about how far from (globally) optimal the
local solution is. Local optimization methods are often sensitive to algorithm parameter values,
which may need to be adjusted for a particular problem, or family of problems.

Using a local optimization method is trickier than solving a least-squares problem, linear program,
or convex optimization problem. It involves experimenting with the choice of algorithm, adjusting
algorithm parameters, and finding a good enough initial guess (when one instance is to be solved) or
a method for producing a good enough initial guess (when a family of problems is to be solved).
Roughly speaking, local optimization methods are more art than technology. Local optimization is a
well developed art, and often very efective, but it is nevertheless an art. In contrast, there is
little art involved in solving a least-squares problem or a linear program (except, of course, those
on the boundary of what is currently possible).

An interesting comparison can be made between local optimization methods for nonlinear programming,
and convex optimization. Since diferentiability of the objective and constraint functions is the
only requirement for most local optimization methods, formulating a practical problem as a nonlinear
optimization problem is relatively straightforward. The art in local optimization is in solving the
problem (in the weakened sense of finding a locally optimal point), once it is formulated. In convex
optimization these are reversed: The art and challenge is in problem formulation; once a problem is
formulated as a convex optimization problem, it is relatively straightforward to solve it.

## 1.4.2 Global optimization

In global optimization, the true global solution of the optimization problem (1.1) is found; the
compromise is eficiency. The worst-case complexity of global optimization methods grows
exponentially with the problem sizes n and m; the hope is that in practice, for the particular
problem instances encountered, the method is far faster. While this favorable situation does occur,
it is not typical. Even small problems, with a few tens of variables, can take a very long time
(e.g., hours or days) to solve.

Global optimization is used for problems with a small number of variables, where computing time is
not critical, and the value of finding the true global solution is very high. One example from
engineering design is worst-case analysis or verification of a high value or safety-critical system.
Here the variables represent uncertain parameters, that can vary during manufacturing, or with the
environment or operating condition. The objective function is a utility function, i.e., one for
which smaller values are worse than larger values, and the constraints represent prior knowledge
about the possible parameter values. The optimization problem (1.1) is the problem of finding the
worst-case values of the parameters. If the worst-case value is acceptable, we can certify the
system as safe or reliable (with respect to the parameter variations).

A local optimization method can rapidly find a set of parameter values that is bad, but not
guaranteed to be the absolute worst possible. If a local optimization method finds parameter values
that yield unacceptable performance, it has succeeded in determining that the system is not
reliable. But a local optimization method cannot certify the system as reliable; it can only fail to
find bad parameter values. A global optimization method, in contrast, will find the absolute worst
values of the parameters, and if the associated performance is acceptable, can certify the system as
safe. The cost is computation time, which can be very large, even for a relatively small number of
parameters. But it may be worth it in cases where the value of certifying the performance is high,
or the cost of being wrong about the reliability or safety is high.

## 1.4.3 Role of convex optimization in nonconvex problems

In this book we focus primarily on convex optimization problems, and applications that can be
reduced to convex optimization problems. But convex optimization also plays an important role in
problems that are not convex.

## Initialization for local optimization

One obvious use is to combine convex optimization with a local optimization method. Starting with a
nonconvex problem, we first find an approximate, but convex, formulation of the problem. By solving
this approximate problem, which can be done easily and without an initial guess, we obtain the exact
solution to the approximate convex problem. This point is then used as the starting point for a
local optimization method, applied to the original nonconvex problem.

## Convex heuristics for nonconvex optimization

Convex optimization is the basis for several heuristics for solving nonconvex problems. One
interesting example we will see is the problem of finding a sparse vector x (i.e., one with few
nonzero entries) that satisfies some constraints. While this is a dificult combinatorial problem,
there are some simple heuristics, based on convex optimization, that often find fairly sparse
solutions. (These are described in chapter 6.)

Another broad example is given by randomized algorithms, in which an approximate solution to a
nonconvex problem is found by drawing some number of candidates from a probability distribution, and
taking the best one found as the approximate solution. Now suppose the family of distributions from
which we will draw the candidates is parametrized, e.g., by its mean and covariance. We can then
pose the question, which of these distributions gives us the smallest expected value of the
objective? It turns out that this problem is sometimes a convex problem, and therefore eficiently
solved. (See, e.g., exercise 11.23.)

## Bounds for global optimization

Many methods for global optimization require a cheaply computable lower bound on the optimal value
of the nonconvex problem. Two standard methods for doing this are based on convex optimization. In
relaxation, each nonconvex constraint is replaced with a looser, but convex, constraint. In
Lagrangian relaxation, the Lagrangian dual problem (described in chapter 5) is solved. This problem
is convex, and provides a lower bound on the optimal value of the nonconvex problem.

## 1.5 Outline

The book is divided into three main parts, titled Theory, Applications, and Algorithms.

## 1.5.1 Part I: Theory

In part I, Theory, we cover basic definitions, concepts, and results from convex analysis and convex
optimization. We make no attempt to be encyclopedic, and skew our selection of topics toward those
that we think are useful in recognizing and formulating convex optimization problems. This is
classical material, almost all of which can be found in other texts on convex analysis and
optimization. We make no attempt to give the most general form of the results; for that the reader
can refer to any of the standard texts on convex analysis.

Chapters 2 and 3 cover convex sets and convex functions, respectively. We give some common examples
of convex sets and functions, as well as a number of convex calculus rules, i.e., operations on sets
and functions that preserve convexity. Combining the basic examples with the convex calculus rules
allows us to form (or perhaps more importantly, recognize) some fairly complicated convex sets and
functions.

In chapter 4, Convex optimization problems, we give a careful treatment of optimization problems,
and describe a number of transformations that can be used to reformulate problems. We also introduce
some common subclasses of convex optimization, such as linear programming and geometric programming,
and the more recently developed second-order cone programming and semidefinite programming.

Chapter 5 covers Lagrangian duality, which plays a central role in convex optimization. Here we give
the classical Karush-Kuhn-Tucker conditions for optimality, and a local and global sensitivity
analysis for convex optimization problems.

## 1.5.2 Part II: Applications

In part II, Applications, we describe a variety of applications of convex optimization, in areas
like probability and statistics, computational geometry, and data fitting. We have described these
applications in a way that is accessible, we hope, to a broad audience. To keep each application
short, we consider only simple cases, sometimes adding comments about possible extensions. We are
sure that our treatment of some of the applications will cause experts to cringe, and we apologize
to them in advance. But our goal is to convey the flavor of the application, quickly and to a broad
audience, and not to give an elegant, theoretically sound, or complete treatment. Our own
backgrounds are in electrical engineering, in areas like control systems, signal processing, and
circuit analysis and design. Although we include these topics in the courses we teach (using this
book as the main text), only a few of these applications are broadly enough accessible to be
included here.

The aim of part II is to show the reader, by example, how convex optimization can be applied in
practice.

## 1.5.3 Part III: Algorithms

In part III, Algorithms, we describe numerical methods for solving convex optimization problems,
focusing on Newton’s algorithm and interior-point methods. Part III is organized as three chapters,
which cover unconstrained optimization, equality constrained optimization, and inequality
constrained optimization, respectively. These chapters follow a natural hierarchy, in which solving
a problem is reduced to solving a sequence of simpler problems. Quadratic optimization problems
(including, e.g., least-squares) form the base of the hierarchy; they can be solved exactly by
solving a set of linear equations. Newton’s method, developed in chapters 9 and 10, is the next
level in the hierarchy. In Newton’s method, solving an unconstrained or equality constrained problem
is reduced to solving a sequence of quadratic problems. In chapter 11, we describe interior-point
methods, which form the top level of the hierarchy. These methods solve an inequality constrained
problem by solving a sequence of unconstrained, or equality constrained, problems.

Overall we cover just a handful of algorithms, and omit entire classes of good methods, such as
quasi-Newton, conjugate-gradient, bundle, and cutting-plane algorithms. For the methods we do
describe, we give simplified variants, and not the latest, most sophisticated versions. Our choice
of algorithms was guided by several criteria. We chose algorithms that are simple (to describe and
implement), but also reliable and robust, and efective and fast enough for most problems.

Many users of convex optimization end up using (but not developing) standard software, such as a
linear or semidefinite programming solver. For these users, the material in part III is meant to
convey the basic flavor of the methods, and give some ideas of their basic attributes. For those few
who will end up developing new algorithms, we think that part III serves as a good introduction.

## 1.5.4 Appendices

There are three appendices. The first lists some basic facts from mathematics that we use, and
serves the secondary purpose of setting out our notation. The second appendix covers a fairly
particular topic, optimization problems with quadratic objective and one quadratic constraint. These
are nonconvex problems that nevertheless can be efectively solved, and we use the results in several
of the applications described in part II.

The final appendix gives a brief introduction to numerical linear algebra, concentrating on methods
that can exploit problem structure, such as sparsity, to gain eficiency. We do not cover a number of
important topics, including roundof analysis, or give any details of the methods used to carry out
the required factorizations. These topics are covered by a number of excellent texts.

## 1.5.5 Comments on examples

In many places in the text (but particularly in parts II and III, which cover applications and
algorithms, respectively) we illustrate ideas using specific examples. In some cases, the examples
are chosen (or designed) specifically to illustrate our point; in other cases, the examples are
chosen to be ‘typical’. This means that the examples were chosen as samples from some obvious or
simple probability distribution. The dangers of drawing conclusions about algorithm performance from
a few tens or hundreds of randomly generated examples are well known, so we will not repeat them
here. These examples are meant only to give a rough idea of algorithm performance, or a rough idea
of how the computational efort varies with problem dimensions, and not as accurate predictors of
algorithm performance. In particular, your results may vary from ours.

## 1.5.6 Comments on exercises

Each chapter concludes with a set of exercises. Some involve working out the details of an argument
or claim made in the text. Others focus on determining, or establishing, convexity of some given
sets, functions, or problems; or more generally, convex optimization problem formulation. Some
chapters include numerical exercises, which require some (but not much) programming in an
appropriate high level language. The dificulty level of the exercises is mixed, and varies without
warning from quite straightforward to rather tricky.

## 1.6 Notation

Our notation is more or less standard, with a few exceptions. In this section we describe our basic
notation; a more complete list appears on page 697.

We use $\mathbf { R } $ to denote the set of real numbers, $\mathbf { R } _ { + }$ to denote the set
of nonnegative real numbers, and $\mathbf { R } _ { + + }$ to denote the set of positive real
numbers. The set of real n-vectors is denoted $\mathbf { R } ^ { n }$ , and the set of real
$m \times n$ matrices is denoted $\mathbf { R } ^ { m \times n }$ . We delimit vectors and matrices
with square brackets, with the components separated by space. We use parentheses to construct column
vectors from comma separated lists. For example, if $a , \ b , \ c \in \mathbf { R }$ , we have

$$
( a , b , c ) = { \left[ \begin{array} { l } { a } \\ { b } \\ { c } \end{array} \right] } = { \left[ \begin{array} { l l l } { a } & { b } & { c } \end{array} \right] } ^ { T } ,
$$

which is an element of $\mathbf { R } ^ { 3 }$ . The symbol 1 denotes a vector all of whose
components are one (with dimension determined from context). The notation $x _ { i }$ can refer to
the ith component of the vector $x ,$ or to the ith element of a set or sequence of vectors
$x _ { 1 } , x _ { 2 } , \dotsc .$ The context, or the text, makes it clear which is meant.

We use $\mathbf { S } ^ { k }$ to denote the set of symmetric $k \times k$ matrices,
$\mathbf { S } _ { + } ^ { k }$ to denote the set of symmetric positive semidefinite $k \times k$
matrices, and $\mathbf { S } _ { + + } ^ { k }$ to denote the set of symmetric positive definite
$k \times k$ matrices. The curled inequality symbol $\succeq$ (and its strict form $\succ$ ) is used
to denote generalized inequality: between vectors, it represents componentwise inequality; between
symmetric matrices, it represents matrix inequality. With a subscript, the symbol $\preceq _ { K }$
(or $\prec _ { K }$) denotes generalized inequality with respect to the cone $K$ (explained in
2.4.1).

Our notation for describing functions deviates a bit from standard notation, but we hope it will
cause no confusion. We use the notation $f : \mathbf { R } ^ { p } \to \mathbf { R } ^ { q }$ to
mean that $f$ is an $\mathbf { R } ^ { q }$-valued function on some subset of
$\mathbf { R } ^ { p }$ , specifically, its domain, which we denote dom $f$. We can think of our use
of the notation $f : \mathbf { R } ^ { p } \to \mathbf { R } ^ { q }$ as a declaration of the
function type, as in a computer language: $f : \mathbf { R } ^ { p } \to \mathbf { R } ^ { q }$
means that the function $f$ takes as argument a real p-vector, and returns a real q-vector. The set
dom $f$, the domain of the function $f $, specifies the subset of $\mathbf { R } ^ { p }$ of points
x for which $f ( x )$ is defined. As an example, we describe the logarithm function as log :
$\mathbf {R} \to \mathbf {R}$ , with dom log = $\mathbf { R } _ { + + }$ The notation log :
$\mathbf {R} \to  \mathbf {R}$ means that the logarithm function accepts and returns a real number;
dom log $=\mathbf {R} _ { ++ }$ means that the logarithm is defined only for positive numbers.

We use $\mathbf { R } ^ { n }$ as a generic finite-dimensional vector space. We will encounter
several other finite-dimensional vector spaces, $e . g .$ , the space of polynomials of a variable
with a given maximum degree, or the space $\mathbf { S } ^ { k }$ of symmetric $k \times k$
matrices. By identifying a basis for a vector space, we can always identify it with
$\mathbf { R } ^ { n }$ (where n is its dimension), and therefore the generic results, stated for
the vector space $\mathbf { R } ^ { n }$ , can be applied. We usually leave it to the reader to
translate general results or statements to other vector spaces. For example, any linear function
$f : \mathbf { R } ^ { n } \to \mathbf { R } $ can be represented in the form
$f ( x ) = c ^ { T } x ,$ where $c \in \mathbf { R } ^ { n }$ . The corresponding statement for the
vector space $\mathbf { S } ^ { k }$ can be found by choosing a basis and translating. This results
in the statement: any linear function $f : \mathbf { S } ^ { k } \to \mathbf { R }$ can be
represented in the form $f ( X ) = \mathbf { tr } ( CX )$ , where $C \in \mathbf { S } ^ { k }$


Part I

Theory

# Chapter 2

# Convex sets

## 2.1 Afine and convex sets

## 2.1.1 Lines and line segments

Suppose $x_1 \neq x_2$ are two points in $\mathbf { R } ^ { n }$ . Points of the form

$$
y = \theta x _ { 1 } + ( 1 - \theta ) x _ { 2 } ,
$$

where $\theta \in \mathbf { R }$ , form the line passing through $x _ { 1 }$ and $x _ { 2 }$ . The
parameter value $\theta = 0$ corresponds to $y = x _ { 2 }$ , and the parameter value $\theta = 1$
corresponds to $y = x _ { 1 }$ Values of the parameter θ between 0 and 1 correspond to the (closed)
line segment between $x _ { 1 }$ and $x _ { 2 }$

Expressing y in the form

$$
y = x _ { 2 } + \theta ( x _ { 1 } - x _ { 2 } )
$$

gives another interpretation: y is the sum of the base point $x _ { 2 }$ (corresponding to
$\theta = 0 )$ and the direction $x _ { 1 } - x _ { 2 }$ (which points from $x _ { 2 }$ to
$x _ { 1 } $) scaled by the parameter $\theta$. Thus, $\theta$ gives the fraction of the way from
$x_2$ to $x_1$ where $y$ lies. As $\theta$ increases from 0 to 1, the point $y$ moves from
$x _ { 2 }$ to $x _ { 1 }$; for $\theta > 1$ , the point $y$ lies on the line beyond $x _ { 1 }$ .
This is illustrated in figure 2.1.

## 2.1.2 Afine sets

A set $C \subseteq \mathbf { R } ^ { n }$ is affine if the line through any two distinct points in
$C$ lies in $C _ { i }$ $i . e .$ , if for any $x _ { 1 } , x _ { 2 } \in C$ and
$\theta \in \mathbf { R }$ , we have $\theta x _ { 1 } + ( 1 - \theta ) x _ { 2 } \in C$ . In other
words, C contains the linear combination of any two points in $C ,$ , provided the coeficients in
the linear combination sum to one.

This idea can be generalized to more than two points. We refer to a point of the form
$\theta _ { 1 } x _ { 1 } + \cdots + \theta _ { k } x _ { k }$ , where
$\theta _ { 1 } + \cdots + \theta _ { k } = 1$ , as an afine combination of the points
$x _ { 1 } , \cdots , x _ { k }$ . Using induction from the definition of afine set ($ i . e .$ that
it contains every afine combination of two points in it), it can be shown that an afine set contains
every afine combination of its points: If $C$ is an afine set,
$x _ { 1 } , \ldots , x _ { k } \in C $. and $\theta _ { 1 } + \cdots + \theta _ { k } = 1$ , then
the point $\theta _ { 1 } x _ { 1 } + \cdots + \theta _ { k } x _ { k }$ also belongs to $C$.


If $C$ is an afine set and $x _ { 0 } \in C$ , then the set

$$
V = C - x _ { 0 } = \{ x - x _ { 0 } | x \in C \}
$$

is a subspace, $i.e . ,$ closed under sums and scalar multiplication. To see this, suppose
$v _ { 1 } , v _ { 2 } \in V$ and $\alpha , \beta \in \mathbf { R }$ . Then we have
$v _ { 1 } + x _ { 0 } \in C$ and $v _ { 2 } + x _ { 0 } \in C$ , and so

$$
\alpha v _ { 1 } + \beta v _ { 2 } + x _ { 0 } = \alpha ( v _ { 1 } + x _ { 0 } ) + \beta ( v _ { 2 } + x _ { 0 } ) + ( 1 - \alpha - \beta ) x _ { 0 } \in C ,
$$

since C is afine, and $\alpha + \beta + ( 1 - \alpha - \beta ) = 1$ . We conclude that
$\alpha v _ { 1 } + \beta v _ { 2 } \in V$ since
$\alpha v _ { 1 } + \beta v _ { 2 } + x _ { 0 } \in C$

Thus, the afine set C can be expressed as

$$
C = V + x _ { 0 } = \{ v + x _ { 0 } | v \in V \} ,
$$

$i . e .$ , as a subspace plus an ofset. The subspace $V$ associated with the afine set $C$ does not
depend on the choice of $x _ { 0 } ,$ so $x _ { 0 }$ can be chosen as any point in $C $. We define
the dimension of an afine set C as the dimension of the subspace $V = C - x _ { 0 }$ ， where
$x _ { 0 }$ is any element of $C$.

Example 2.1 Solution set of linear equations. The solution set of a system of linear equations,
$C = \{ x | A x = b \}$ , where $A \in \mathbf { R } ^ { m \times n }$ and
$b \in \mathbf { R } ^ { m }$ , is an afine set. To show this, suppose
$x_1, x_2 \in C, i.e., Ax_1 = b, Ax_2 = b$. Then for any $\theta $, we have

$$
\begin{array} { l c l } { { A ( \theta x _ { 1 } + ( 1 - \theta ) x _ { 2 } ) } } & { { = } } & { { \theta A x _ { 1 } + ( 1 - \theta ) A x _ { 2 } } } \\ { { } } & { { = } } & { { \theta b + ( 1 - \theta ) b } } \\ { { } } & { { = } } & { { b , } } \end{array}
$$

which shows that the afine combination $\theta x _ { 1 } + ( 1 - \theta ) x _ { 2 }$ is also in $C$.
The subspace associated with the afine set $C$ is the nullspace of $A$.

We also have a converse: every afine set can be expressed as the solution set of a system of linear
equations.

The set of all afine combinations of points in some set $C \subseteq \mathbf { R } ^ { n }$ is
called the afine hull of $C $, and denoted :

$\mathbf{aff}C$ :

$$
\mathbf{aff}C = \{ \theta _ { 1 } x _ { 1 } + \cdots + \theta _ { k } x _ { k }  |  x _ { 1 } , \ldots , x _ { k } \in C ,  \theta _ { 1 } + \cdots + \theta _ { k } = 1 \} .
$$

The afine hull is the smallest afine set that contains $C ,$ in the following sense: if $S$ is any
afine set with $C \subseteq S$ , then $\mathbf{aff}C \subseteq S$

## 2.1.3 Afine dimension and relative interior

We define the affine dimension of a set $C$ as the dimension of its affine hull. Affine dimension is
useful in the context of convex analysis and optimization, but is not always consistent with other
definitions of dimension. As an example consider the unit circle in
$ \mathbf{R}  ^ { 2 } , i . e . , \{ x \in \mathbf{R}  ^ { 2 } | x _ { 1 } ^ { 2 } + x _ { 2 } ^ { 2 } = 1 \}$ .
Its afine hull is all of $\mathbf { R } ^ { 2 }$ , so its affine dimension is two. By most
definitions of dimension, however, the unit circle in $\mathbf { R } ^ { 2 }$ has dimension one.

If the affine dimension of a set $C \subseteq \mathbf { R } ^ { n }$ is less than $n$, then the set
lies in the afine set $\mathbf{aff} C \neq \mathbf { R } ^ { n }$ . We define the relative interior
of the set $C ,$ , denoted relint C, as its interior relative to $\mathbf{aff} C $:

$$
\mathbf {relint} C = \{ x \in C \mid B ( x , r ) \cap \mathbf {aff} C \subseteq C { \mathrm {for some} } r > 0 \} ,
$$

where $B ( x , r ) = \{ y \mid \| y - x \| \leq r \}$ , the ball of radius $r$ and center $x$ in the
norm $\| \cdot \| .$ (Here $\| \cdot \|$ is any norm; all norms define the same relative interior.)
We can then define the relative boundary of a set $C$ as $\mathbf{cl} C \backslash$ relint $C ,$ ,
where $\mathbf{cl} C$ is the closure of $C .$

Example 2.2 Consider a square in the $( x _ { 1 } , x _ { 2 } )$ -plane in
$\mathbf { R } ^ { 3 } { \mathrm { . } }$ , defined as

$$
C = \{ x \in \mathbf { R }  ^ { 3 } \mid - 1 \leq x _ { 1 } \leq 1 , - 1 \leq x _ { 2 } \leq 1 , x _ { 3 } = 0 \} .
$$

Its afine hull is the $( x _ { 1 } , x _ { 2 } )$ -plane, $i . e . ,$ ,
$\mathbf {aff} C = \{ x \in \mathbf { R } ^ { 3 } \mid x _ { 3 } = 0 \}$ . The interior of $C$ is
empty, but the relative interior is

$$
\mathbf { relint } C = \{ x \in \mathbf { R } ^ { 3 } \mid - 1 < x _ { 1 } < 1 , - 1 < x _ { 2 } < 1 , x _ { 3 } = 0 \} .
$$

Its boundary $( \mathrm { in } \ \mathbf { R } ^ { 3 } )$ is itself; its relative boundary is the
wire-frame outline,

$$
\{ x \in \mathbf { R } ^ { 3 } \mid \operatorname* { max } \{ | x _ { 1 } | , | x _ { 2 } | \} = 1 , x _ { 3 } = 0 \} .
$$

## 2.1.4 Convex sets

A set C is convex if the line segment between any two points in C lies in $C , i . e .$ if for any
$x _ { 1 } , x _ { 2 } \in C$ and any $\theta$ with $0 \leq \theta \leq 1$ , we have

$$
\theta x _ { 1 } + ( 1 - \theta ) x _ { 2 } \in C .
$$

Roughly speaking, a set is convex if every point in the set can be seen by every other point, along
an unobstructed straight path between them, where unobstructed means lying in the set. Every afine
set is also convex, since it contains the entire line between any two distinct points in it, and
therefore also the line segment between the points. Figure 2.2 illustrates some simple convex and
nonconvex sets in $\mathbf { R } ^ { 2 }$

We call a point of the form $\theta _ { 1 } x _ { 1 } + \cdots + \theta _ { k } x _ { k }$ , where
$\theta _ { 1 } + \cdots + \theta _ { k } = 1$ and $\theta _ { i } \geq 0 , i = 1 , \ldots , k .$ a
convex combination of the points $x _ { 1 } , \ldots , x _ { k }$ . As with afine sets, it can be
shown that a set is convex if and only if it contains every convex combination of its points. A
convex combination of points can be thought of as a mixture or weighted average of the points, with
$\theta _ { i }$ the fraction of $x _ { i }$ in the mixture.

The convex hull of a set $C ,$ denoted conv $C ,$ is the set of all convex combinations of points in
$C$:

$$
\mathbf {conv} C = \{ \theta _ { 1 } x _ { 1 } + \cdots + \theta _ { k } x _ { k } \mid x _ { i } \in C , \theta _ { i } \geq 0 ,  i = 1 , \cdots , k ,  \theta _ { 1 } + \cdots + \theta _ { k } = 1 \} .
$$

As the name suggests, the convex hull $\mathbf {conv} C$ is always convex. It is the smallest convex
set that contains $C$: If B is any convex set that contains $C _ { i }$ , then
$\mathbf {conv} C \subseteq B$. Figure 2.3 illustrates the definition of convex hull.

The idea of a convex combination can be generalized to include infinite sums, integrals, and, in the
most general form, probability distributions. Suppose $\theta _ { 1 } , \theta _ { 2 } , \ldots$

satisfy

$$
\theta _ { i } \geq 0 , \quad i = 1 , 2 , \ldots , \qquad \sum _ { i = 1 } ^ { \infty } \theta _ { i } = 1 ,
$$

and $x _ { 1 } , x _ { 2 } , \ldots \in C$ , where $C \subseteq \mathbf { R } ^ { n }$ is convex.
Then

$$
\sum _ { i = 1 } ^ { \infty } \theta _ { i } x _ { i } \in C ,
$$

if the series converges. More generally, suppose $p : \mathbf { R } ^ { n } \to \mathbf { R }$
satisfies $p ( x ) \geq 0$ for all $x \in C$ and $\int _ { C } p ( x ) d x = 1$ , where
$C \subseteq \mathbf { R } ^ { n }$ is convex. Then

$$
\int _ { C } p ( x ) x dx \in C ,
$$

if the integral exists.

In the most general form, suppose $C \subseteq \mathbf { R } ^ { n }$ is convex and x is a random
vector with $x \in C$ with probability one. Then $\mathbf { E } x \in C$ . Indeed, this form
includes all the others as special cases. For example, suppose the random variable x only takes on
the two values $x _ { 1 }$ and $x _ { 2 }$ , with $\mathbf{prob}$ ( $x = x _ { 1 }$ ) $= \theta$ and
$\mathbf{prob} $($ x = x _ { 2 }$ ) $= 1 - \theta ,$ where $0 \leq \theta \leq 1$ . Then
$\mathbf { E } x = \theta x _ { 1 } + ( 1 - \theta ) x _ { 2 }$ , and we are back to a simple convex
combination of two points.

## 2.1.5 Cones

A set $C$ is called a cone, or nonnegative homogeneous, if for every $x \in C$ and $\theta \geq 0$
we have $\theta x \in C$ . A set $C$ is a convex cone if it is convex and a cone, which means that
for any $x _ { 1 } ,  x _ { 2 } \in C$ and $\theta _ { 1 } ,  \theta _ { 2 } \geq 0$ , we have

$$
\theta _ { 1 } x _ { 1 } + \theta _ { 2 } x _ { 2 } \in C .
$$

Points of this form can be described geometrically as forming the two-dimensional pie slice with
apex 0 and edges passing through $x _ { 1 }$ and $x _ { 2 }$ . (See figure 2.4.)

A point of the form $\theta _ { 1 } x _ { 1 } + \cdot \cdot \cdot + \theta _ { k } x _ { k }$ with
$\theta _ { 1 } , \ldots , \theta _ { k }  \geq 0$ is called a conic combination (or a nonnegative
linear combination) of $x _ { 1 } , \ldots , x _ { k }$ . If $x _ { i }$ are in a convex cone $C ,$
then every conic combination of $x _ { i }$ is in $C .$ . Conversely, a set $C$ is a convex cone if
and only if it contains all conic combinations of its elements. Like convex (or afine) combinations,
the idea of conic combination can be generalized to infinite sums and integrals.

The conic hull of a set $C$ is the set of all conic combinations of points in $C , i . e .$

$$
\{ \theta _ { 1 } x _ { 1 } + \cdot \cdot \cdot + \theta _ { k } x _ { k } \mid x _ { i } \in C ,  \theta _ { i } \geq 0 , i = 1 , \ldots , k \} ,
$$

which is also the smallest convex cone that contains C (see figure 2.5).

## 2.2 Some important examples

In this section we describe some important examples of convex sets which we will encounter
throughout the rest of the book. We start with some simple examples.

The empty set $\varnothing ,$ any single point ($ i . e . ,$ singleton) $\{ x _ { 0 } \}$ , and the
whole space $\mathbf { R } ^ { n }$ are afine (hence, convex) subsets of $\mathbf { R } ^ { n }$

Any line is afine. If it passes through zero, it is a subspace, hence also a convex cone.

A line segment is convex, but not afine (unless it reduces to a point).

A ray, which has the form $\{ x _ { 0 } + \theta v \mid \theta \geq 0 \}$ , where $v \neq 0$ , is
convex, but not afine. It is a convex cone if its base $x _ { 0 }$ is 0.

Any subspace is afine, and a convex cone (hence convex).

## 2.2.1 Hyperplanes and halfspaces

A hyperplane is a set of the form

$$
\{ x \mid a ^ { T } x = b \} ,
$$

where $a \in \mathbf { R } ^ { n } , a \neq 0 .$ , and $b \in \mathbf { R }$ . Analytically it is
the solution set of a nontrivial linear equation among the components of $x$ (and hence an afine
set). Geometrically, the hyperplane $\{ x \mid a ^ { T } x = b \}$ can be interpreted as the set of
points with a constant inner product to a given vector $a$, or as a hyperplane with normal vector
$a$; the constant $b \in \mathbf { R }$ determines the ofset of the hyperplane from the origin. This
geometric interpretation can be understood by expressing the hyperplane in the form

$$
\{ x \mid a ^ { T } ( x - x _ { 0 } ) = 0 \} ,
$$

where $x _ { 0 }$ is any point in the hyperplane ($ i . e . ,$ , any point that satisfies
$a ^ { T } x _ { 0 } = b $) This representation can in turn be expressed as

$$
\{ x \mid a ^ { T } ( x - x _ { 0 } ) = 0 \} = x _ { 0 } + a ^ { \perp } ,
$$

where $a ^ { \perp }$ denotes the orthogonal complement of $a , i . e .$ , the set of all vectors
orthogonal to it:

$$
a ^ { \perp } = \{ v \mid a ^ { T } v = 0 \} .
$$

This shows that the hyperplane consists of an ofset $x _ { 0 } .$ , plus all vectors orthogonal to
the (normal) vector $a .$ These geometric interpretations are illustrated in figure 2.6.

A hyperplane divides $\mathbf { R } ^ { n }$ into two halfspaces. A (closed) halfspace is a set of
the form

$$
\{ x \mid a ^ { T } x \leq b \} ,\tag{2.1}
$$

where $a \neq 0 , i . e .$ , the solution set of one (nontrivial) linear inequality. Halfspaces are
convex, but not afine. This is illustrated in figure 2.7.


The halfspace (2.1) can also be expressed as

$$
\{ x \mid a ^ { T } ( x - x _ { 0 } ) \leq 0 \} ,\tag{2.2}
$$

where $x _ { 0 }$ is any point on the associated hyperplane, i.e., satisfies
$a ^ { T } x _ { 0 } = b .$ . The representation (2.2) suggests a simple geometric interpretation:
the halfspace consists of $x _ { 0 }$ plus any vector that makes an obtuse (or right) angle with the
(outward normal) vector a. This is illustrated in figure 2.8.

The boundary of the halfspace (2.1) is the hyperplane $\{ x \mid a ^ { T } x = b \}$ . The set
$\{ x \mid a ^ { T } x < b \}$ , which is the interior of the halfspace
$\{ x \mid  a ^ { T } x \leq b \}$ , is called an open halfspace.

## 2.2.2 Euclidean balls and ellipsoids

A (Euclidean) ball (or just ball) in $\mathbf { R } ^ { n }$ has the form

$$
B ( x _ { c } , r ) = \{ x \mid \| x - x _ { c } \| _ { 2 } \leq r \} = \{ x \mid ( x - x _ { c } ) ^ { T } ( x - x _ { c } ) \leq r ^ { 2 } \} ,
$$

where $r > 0$ , and $\| \cdot \| _ { 2 }$ denotes the Euclidean norm, i.e.,
$\| u \| _ { 2 } = ( u ^ { T } u ) ^ { 1 / 2 }$ . The vector $x _ { c }$ is the center of the ball
and the scalar $r$ is its radius; $ B(x_{ c },r)$ consists of all points within a distance $r$ of
the center $x _ { c } .$ . Another common representation for the Euclidean ball is

$$
B ( x _ { c } , r ) = \{ x _ { c } + r u \mid \| u \| _ { 2 } \leq 1 \} .
$$

A Euclidean ball is a convex set: if $\| x _ { 1 } - x _ { c } \| _ { 2 } \leq r , \| x _ { 2 } - x _ { c } \| _ { 2 } \leq r $ ,
and $0 \leq \theta \leq 1$ , then

$$
\begin{array} { l c l } { \| \theta x _ { 1 } + ( 1 - \theta ) x _ { 2 } - x _ { c } \| _ { 2 } } & { = } & { \| \theta ( x _ { 1 } - x _ { c } ) + ( 1 - \theta ) ( x _ { 2 } - x _ { c } ) \| _ { 2 } } \\ & { \leq } & { \theta \| x _ { 1 } - x _ { c } \| _ { 2 } + ( 1 - \theta ) \| x _ { 2 } - x _ { c } \| _ { 2 } } \\ & { \leq } & { r . } \end{array}
$$

(Here we use the homogeneity property and triangle inequality for $\| \cdot \| _ { 2 } ;$ see
A.1.2.) A related family of convex sets is the ellipsoids, which have the form

$$
\epsilon = \{ x \mid ( x - x _ { c } ) ^ { T } P ^ { - 1 } ( x - x _ { c } ) \leq 1 \} ,\tag{2.3}
$$

where $P = P ^ { T } \succ 0 , i . e . , P$ is symmetric and positive definite. The vector
$x _ { c } \in \mathbf { R } ^ { n }$ is the center of the ellipsoid. The matrix $P$ determines how
far the ellipsoid extends in every direction from $x _ { c } $; the lengths of the semi-axes of
$\epsilon$ are given by $\sqrt { \lambda _ { i } } ,$ , where $\lambda _ { i }$ are the eigenvalues
of $P$. A ball is an ellipsoid with $P = r ^ { 2 } I$ . Figure 2.9 shows an ellipsoid in
$\mathbf { R } ^ { 2 }$

Another common representation of an ellipsoid is

$$
\epsilon = \{ x _ { c } + A u \mid \| u \| _ { 2 } \leq 1 \} ,\tag{2.4}
$$

where A is square and nonsingular. In this representation we can assume without loss of generality
that A is symmetric and positive definite. By taking $A = P ^ { 1 / 2 }$ this representation gives
the ellipsoid defined in (2.3). When the matrix A in (2.4) is symmetric positive semidefinite but
singular, the set in (2.4) is called a degenerate ellipsoid; its afine dimension is equal to the
rank of A. Degenerate ellipsoids are also convex.

## 2.2.3 Norm balls and norm cones

Suppose $\|\cdot\|$ is any norm on $\mathbf { R } ^ { n }$ (see A.1.2). From the general properties
of norms it can be shown that a norm ball of radius r and center $x _ { c } ,$ given by
$\{ x \mid \| x - x _ { c } \| \leq r \}$ ， is convex. The norm cone associated with the norm
$\| \cdot \|$ is the set

$$
C = \{ ( x , t ) \mid \| x \| \leq t \} \subseteq \mathbf { R } ^ { n + 1 } .
$$

It is (as the name suggests) a convex cone.

Example 2.3 The second-order cone is the norm cone for the Euclidean norm, $i . e . ,$

$$
\begin{array} { r c l } { C } & { = } & { \{ ( x , t ) \in  \mathbf {R} ^ { n + 1 } \mid \| x \| _ { 2 } \leq t \} } \\ & { = } & { \left\{ \left[ \begin{array} { l } { x } \\ { t } \end{array} \right] \ \left[ \begin{array} { c } { x } \\ { t } \end{array} \right] ^ { T } \left[ \begin{array} { c c } { I } & { 0 } \\ { 0 } & { - 1 } \end{array} \right] \left[ \begin{array} { c } { x } \\ { t } \end{array} \right] \leq 0 ,  t \geq 0 \right\} . } \end{array}
$$

The second-order cone is also known by several other names. It is called the quadratic cone, since
it is defined by a quadratic inequality. It is also called the Lorentz cone or ice-cream cone.
Figure 2.10 shows the second-order cone in $\mathbf { R } ^ { 3 }$

## 2.2.4 Polyhedra

A polyhedron is defined as the solution set of a finite number of linear equalities and
inequalities:

$$
{ \mathcal { P } } = \{ x \mid a _ { j } ^ { T } x \leq b _ { j } , j = 1 , \ldots , m , c _ { j } ^ { T } x = d _ { j } , j = 1 , \ldots , p \} .\tag{2.5}
$$

A polyhedron is thus the intersection of a finite number of halfspaces and hyperplanes. Afine sets
(e.g., subspaces, hyperplanes, lines), rays, line segments, and halfspaces are all polyhedra. It is
easily shown that polyhedra are convex sets. A bounded polyhedron is sometimes called a polytope,
but some authors use the opposite convention (i.e., polytope for any set of the form (2.5), and
polyhedron when it is bounded). Figure 2.11 shows an example of a polyhedron defined as the
intersection of five halfspaces.

It will be convenient to use the compact notation

$$
\mathcal { P } = \{ x \mid A x \preceq b , Cx = d \}\tag{2.6}
$$

for (2.5), where

$$
A = \left[ \begin{array} { c } { { a _ { 1 } ^ { T } } } \\ { { \vdots } } \\ { { a _ { m } ^ { T } } } \end{array} \right] , \qquad C = \left[ \begin{array} { c } { { c _ { 1 } ^ { T } } } \\ { { \vdots } } \\ { { c _ { p } ^ { T } } } \end{array} \right] ,
$$

and the symbol $\preceq$ denotes vector inequality or componentwise inequality in
$\mathbf { R } ^ { m }$ : $u \preceq v$ means $u _ { i } \leq v _ { i }$ for $i = 1 , \ldots , m$

Example 2.4 The nonnegative orthant is the set of points with nonnegative components, i.e.,

$$
\mathbf { R } _ { + } ^ { n } = \{ x \in \mathbf { R } ^ { n } \mid x _ { i } \geq 0 , i = 1 , \ldots , n \} = \{ x \in \mathbf { R } ^ { n } \mid x \succeq 0 \} .
$$

(Here $\mathbf { R } _ { + }$ denotes the set of nonnegative numbers:
$\mathbf { R } _ { + } = \{ x \in \mathbf { R } \mid x \geq 0 \} . )$ The nonnegative orthant is a
polyhedron and a cone (and therefore called a polyhedral cone).

## Simplexes

Simplexes are another important family of polyhedra. Suppose the $k + 1$ points
$v _ { 0 } , \ldots , v _ { k }  \in \mathbf { R } ^ { n }$ are afinely independent, which means
$v _ { 1 } - v _ { 0 } , \ldots , v _ { k } - v _ { 0 }$ are linearly independent. The simplex
determined by them is given by

$$
C = \mathbf { conv } \{ v _ { 0 } , \ldots , v _ { k } \} = \{ \theta _ { 0 } v _ { 0 } + \cdots + \theta _ { k } v _ { k }  |  \theta \succeq 0 , \mathbf {1} ^ { T } \theta = 1 \} ,\tag{2.7}
$$

where 1 denotes the vector with all entries one. The afine dimension of this simplex is $k$, so it
is sometimes referred to as a k-dimensional simplex in $\mathbf { R } ^ { n }$

Example 2.5 Some common simplexes. A 1-dimensional simplex is a line segment; a 2-dimensional
simplex is a triangle (including its interior); and a 3-dimensional simplex is a tetrahedron.

The unit simplex is the n-dimensional simplex determined by the zero vector and the unit vectors,
$i . e . , 0 ,  e _ { 1 } , \ldots , e _ { n } \in \mathbf { R } ^ { n }$ . It can be expressed as
the set of vectors that satisfy

$$
x \succeq 0 , \qquad 1 ^ { T } x \leq 1 .
$$

The probability simplex is the $( n - 1 )$ -dimensional simplex determined by the unit vectors
$e _ { 1 } , \ldots , e _ { n } \in \mathbf { R } ^ { n }$ . It is the set of vectors that satisfy

$$
x \succeq 0 , \qquad  1 ^ { T } x = 1 .
$$

Vectors in the probability simplex correspond to probability distributions on a set with n elements,
with $x _ { i }$ interpreted as the probability of the ith element.

To describe the simplex (2.7) as a polyhedron, $i . e .$ , in the form (2 . 6), we proceed as
follows. By definition, $x \in C$ if and only if
$x = \theta _ { 0 } v _ { 0 } + \theta _ { 1 } v _ { 1 } + \cdot \cdot \cdot + \theta _ { k } v _ { k }$
for some $\theta \succeq 0$ with $\mathbf { 1 } ^ { T } \theta = 1$ . Equivalently, if we define
$y = ( \theta _ { 1 } , \ldots , \theta _ { k } )$ and

$$
B =  {\left[ \begin{array} { l l l } { v _ { 1 } - v _ { 0 } } & { \cdots } & { v _ { k } - v _ { 0 } } \end{array} \right] } \in \mathbf { R } ^ { n \times k } ,
$$

we can say that $x \in C$ if and only if

$$
x = v _ { 0 } + B y\tag{2.8}
$$

for some $y \succeq 0$ with $\mathbf { 1 } ^ { T } y \leq 1$ . Now we note that afine independence
of the points $v _ { 0 } , \ldots , v _ { k }$ implies that the matrix B has rank k. Therefore there
exists a nonsingular matrix $A = \left( A _ { 1 } , A _ { 2 } \right) \in \mathbf { R } ^ { n \times n }$
such that

$$
A B = { \left[ \begin{array} { l } { A _ { 1 } } \\ { A _ { 2 } } \end{array} \right] } B = { \left[ \begin{array} { l } { I } \\ { 0 } \end{array} \right] } .
$$

Multiplying (2.8) on the left with $A ,$ we obtain

$$
A _ { 1 } x = A _ { 1 } v _ { 0 } + y , \qquad A _ { 2 } x = A _ { 2 } v _ { 0 } .
$$

From this we see that $x \in C$ if and only if $A _ { 2 } x   =   A _ { 2 } v _ { 0 }$ , and the
vector $y = A _ { 1 } x - A _ { 1 } v _ { 0 }$ satisfies $y \succeq 0$ and
$\mathbf { 1 } ^ { T } y \leq 1$ . In other words we have $x \in C$ if and only if

$$
A _ { 2 } x = A _ { 2 } v _ { 0 } , \qquad A _ { 1 } x \succeq A _ { 1 } v _ { 0 } , \qquad  1 ^ { T } A _ { 1 } x \leq 1 +  1 ^ { T } A _ { 1 } v _ { 0 } ,
$$

which is a set of linear equalities and inequalities in x, and so describes a polyhedron.

## Convex hull description of polyhedra

The convex hull of the finite set $\{ v _ { 1 } , \ldots , v _ { k } \}$ is

$$
\mathbf { conv } \{ v _ { 1 } , \ldots , v _ { k } \} = \{ \theta _ { 1 } v _ { 1 } + \cdot \cdot \cdot + \theta _ { k } v _ { k }  | \theta \succeq 0 ,  \mathbf { 1 } ^ { T } \theta = 1 \} .
$$

This set is a polyhedron, and bounded, but (except in special cases, $e . g .$ , a simplex) it is
not simple to express it in the form (2.5), i.e., by a set of linear equalities and inequalities.

A generalization of this convex hull description is

$$
\{ \theta _ { 1 } v _ { 1 } + \cdots + \theta _ { k } v _ { k } \mid \theta _ { 1 } + \cdots + \theta _ { m } = 1 ,  \theta _ { i } \geq 0 ,  i = 1 , \ldots , k \} ,\tag{2.9}
$$

where $m \leq k$ . Here we consider nonnegative linear combinations of $v _ { i }$ , but only the
first m coeficients are required to sum to one. Alternatively, we can interpret (2.9) as the convex
hull of the points $v _ { 1 } , \ldots , v _ { m }$ , plus the conic hull of the points
$v _ { m + 1 } , \ldots , v _ { k }$ . The set (2.9) defines a polyhedron, and conversely, every
polyhedron can be represented in this form (although we will not show this).

The question of how a polyhedron is represented is subtle, and has very important practical
consequences. As a simple example consider the unit ball in the
$\ell _ { \infty } { \mathrm { - n o r m } }$ in $\mathbf { R } ^ { n }$ ,

$$
C = \{ x \mid | x _ { i } | \leq 1 , i = 1 , . . . , n \} .
$$

The set $C$ can be described in the form (2.5) with 2n linear inequalities
$\pm e _ { i } ^ { T } x \le 1$ where $e _ { i }$ is the $i$th unit vector. To describe it in the
convex hull form (2.9) requires at least $2 ^ { n }$ points:

$$
C = \mathbf {conv} \{ v _ { 1 } , \ldots , v _ { 2 ^ { n } } \} ,
$$

where $v _ { 1 } , \ldots , v _ { 2 ^ { n } }$ are the $2 ^ { n }$ vectors all of whose components
are 1 or -1 . Thus the size of the two descriptions difers greatly, for large n.

## 2.2.5 The positive semidefinite cone

We use the notation $\mathbf { S } ^ { n }$ to denote the set of symmetric $n \times n$ matrices,

$$
\mathbf { S } ^ { n } = \{ X \in \mathbf { R } ^ { n \times n } \mid X = X ^ { T } \} ,
$$

which is a vector space with dimension $n ( n + 1 ) / 2$ . We use the notation
$\mathbf { S } _ { + } ^ { n }$ to denote the set of symmetric positive semidefinite matrices:

$$
\mathbf { S } _ { + } ^ { n } = \{ X \in \mathbf { S } ^ { n } \mid X \succeq 0 \} ,
$$

and the notation $\mathbf { S } _ { + + } ^ { n }$ to denote the set of symmetric positive definite
matrices:

$$
\mathbf { S } _ { + + } ^ { n } = \{ X \in \mathbf { S } ^ { n } \mid X \succ 0 \} .
$$

(This notation is meant to be analogous to $\mathbf { R } _ { + }$ , which denotes the nonnegative
reals, and $\mathbf { R } _ { + + }$ , which denotes the positive reals.)

The set $\mathbf { S } _ { + } ^ { n }$ is a convex cone: if
$\theta _ { 1 } , \theta _ { 2 } \geq 0$ and $A, B \in \mathbf { S } _ { + } ^ { n }$ , then
$\theta _ { 1 } A + \theta _ { 2 } B \in \mathbf { S } _ { + } ^ { n }$ This can be seen directly
from the definition of positive semidefiniteness: for any $x \in \mathbf { R } ^ { n }$ , we have

$$
x ^ { T } ( \theta _ { 1 } A + \theta _ { 2 } B ) x = \theta _ { 1 } x ^ { T } A x + \theta _ { 2 } x ^ { T } B x \geq 0 ,
$$

if $A \succeq 0 , B \succeq 0$ and $\theta _ { 1 } , \theta _ { 2 } \geq 0$

Example 2.6 Positive semidefinite cone in $\mathbf { S } ^ { 2 }$ . We have

$$
X = { \left[ \begin{array} { l l } { x } & { y } \\ { y } & { z } \end{array} \right] } \in \mathbf { S } _ { + } ^ { 2 } \quad \Longleftrightarrow \quad x \geq 0 , \quad z \geq 0 , \quad x z \geq y ^ { 2 } .
$$

The boundary of this cone is shown in figure 2.12, plotted in $\mathbf { R } ^ { 3 }$ as
$( x , y , z )$

## 2.3 Operations that preserve convexity

In this section we describe some operations that preserve convexity of sets, or allow us to
construct convex sets from others. These operations, together with the simple examples described in
2.2, form a calculus of convex sets that is useful for determining or establishing convexity of
sets.

## 2.3.1 Intersection

Convexity is preserved under intersection: if $S _ { 1 }$ and $S _ { 2 }$ are convex, then
$S _ { 1 } \cap S _ { 2 }$ is convex. This property extends to the intersection of an infinite
number of sets: if $S _ { \alpha }$ is convex for every $\alpha \in { \mathcal { A } } .$ , then
$\cap _ { \alpha \in A } S _ { \alpha }$ is convex. (Subspaces, afine sets, and convex cones are
also closed under arbitrary intersections.) As a simple example, a polyhedron is the intersection of
halfspaces and hyperplanes (which are convex), and therefore is convex.

Example 2.7 The positive semidefinite cone $\mathbf { S } _ { + } ^ { n }$ can be expressed as

$$
\bigcap _ { z \neq 0 } \{ X \in \mathbf { S } ^ { n } \mid z ^ { T } X z \geq 0 \} .
$$

For each $z \neq 0 , z ^ { T } X z$ is a (not identically zero) linear function of X, so the sets

$$
\{ X \in \mathbf { S } ^ { n } \mid z ^ { T } X z \ge 0 \}
$$

are, in fact, halfspaces in $\mathbf { S } ^ { n }$ . Thus the positive semidefinite cone is the
intersection of an infinite number of halfspaces, and so is convex.

Example 2.8 We consider the set

$$
S = \{ x \in \mathbf { R } ^ { m } \mid | p ( t ) | \leq 1 { \mathrm { for} } | t | \leq \pi / 3 \} ,\tag{2.10}
$$

where $ p ( t ) = \sum _ { k = 1 } ^ { m } x _ { k } \cos {kt} $ . The set $S$ can be expressed as
the intersection of an infinite number of slabs:
$ S = \bigcap _ { | t | \leq \pi / 3 } S _ { t } $ , where

$$
S _ { t } = \{ x \mid - 1 \leq ( \cos t , \ldots , \cos {mt} ) ^ { T } x \leq 1 \} ,
$$

and so is convex. The definition and the set are illustrated in figures 2.13 and 2.14, for $m = 2 .$

In the examples above we establish convexity of a set by expressing it as a (possibly infinite)
intersection of halfspaces. We will see in 2.5.1 that a converse holds: every closed convex set $S$
is a (usually infinite) intersection of halfspaces. In fact, a closed convex set $S$ is the
intersection of all halfspaces that contain it:

$$
S = \bigcap \{ { \mathcal { H } } \mid { \mathcal { H } } { \mathrm {halfspace,} } S \subseteq { \mathcal { H } } \} .
$$

## 2.3.2 Afine functions

Recall that a function $f : \mathbf { R } ^ { n } \to \mathbf { R } ^ { m }$ is affine if it is a
sum of a linear function and a constant, $i . e .$ , if it has the form $f ( x ) = A x + b$ , where
$A \in \mathbf { R } ^ { m \times n }$ and $b \in \mathbf { R } ^ { m }$ Suppose
$S \subseteq \mathbf { R } ^ { n }$ is convex and
$f : \mathbf { R } ^ { n } \to \mathbf { R } ^ { m }$ is an afine function. Then the image of S
under $f,$

$$
f ( S ) = \{ f ( x ) \mid x \in S \} ,
$$

is convex. Similarly, if $f : \mathbf { R } ^ { k } \to \mathbf { R } ^ { n }$ is an afine function,
the inverse image of $S$ under $f ,$

$$
f ^ { - 1 } ( S ) = \{ x \mid f ( x ) \in S \} ,
$$

is convex.

Two simple examples are scaling and translation. If $S \subseteq \mathbf { R } ^ { n }$ is convex,
then the sets $\alpha S$ and $S + a$ are convex, where

$$
\alpha S = \{ \alpha x \mid x \in S \} , \qquad S + a = \{ x + a \mid x \in S \} .
$$

The projection of a convex set onto some of its coordinates is convex: , then

$$
T = \{ x _ { 1 } \in \mathbf { R } ^ { m } \mid ( x _ { 1 } , x _ { 2 } ) \in S { \mathrm { for some } } x _ { 2 } \in \mathbf { R } ^ { n } \}
$$

is convex.

The sum of two sets is defined as

$$
S _ { 1 } + S _ { 2 } = \{ x + y  |  x \in S _ { 1 } ,  y \in S _ { 2 } \} .
$$

If $S _ { 1 }$ and $S _ { 2 }$ are convex, then $S _ { 1 } + S _ { 2 }$ is convex. To see this, if
$S _ { 1 }$ and $S _ { 2 }$ are convex, then so is the direct or Cartesian product

$$
S _ { 1 } \times S _ { 2 } = \{ ( x _ { 1 } , x _ { 2 } ) \mid x _ { 1 } \in S _ { 1 } , \ x _ { 2 } \in S _ { 2 } \} .
$$

The image of this set under the linear function $f ( x _ { 1 } , x _ { 2 } ) = x _ { 1 } + x _ { 2 }$
is the sum $S _ { 1 } + S _ { 2 }$

We can also consider the partial sum of $S _ { 1 } , \ S _ { 2 } \in \mathbf { R } ^ { n } \times \mathbf { R } ^ { m }$ ,
defined as

$$
S = \{ ( x , y _ { 1 } + y _ { 2 } ) \mid ( x , y _ { 1 } ) \in S _ { 1 } , ( x , y _ { 2 } ) \in S _ { 2 } \} ,
$$

where $x \in \mathbf { R } ^ { n }$ and $y _ { i } \in \mathbf { R } ^ { m }$ . For $m = 0$ , the
partial sum gives the intersection of $S _ { 1 }$ and $S _ { 2 } $; for $n = 0$ , it is set
addition. Partial sums of convex sets are convex (see exercise 2.16).

Example 2.9 Polyhedron. The polyhedron $\{ x \mid A x \preceq b , C x = d \}$ can be expressed as
the inverse image of the Cartesian product of the nonnegative orthant and the origin under the afine
function $f ( x ) = ( b - A x , d - C x )$ :

$$
\{ x \mid A x \preceq b , C x = d \} = \{ x \mid f ( x ) \in \mathbf { R } _ { + } ^ { m } \times \{ 0 \} \} .
$$

Example 2.10 Solution set of linear matrix inequality. The condition   
$A ( x ) = x _ { 1 } A _ { 1 } + \cdots + x _ { n } A _ { n } \preceq B ,$ (2.11)   
where $B , A _ { i } \in \mathbf { S } ^ { m }$ , is called a linear matrix inequality (LMI) in x.
(Note the similarity   
to an ordinary linear inequality,   
$a ^ { T } x = x _ { 1 } a _ { 1 } + \cdots + x _ { n } a _ { n } \leq b ,$   
with $b , a_{i} \in \mathbf { R } . $)   
The solution set of a linear matrix inequality, $\{ x \mid A ( x ) \preceq B \}$ , is convex.
Indeed, it is the inverse image of the positive semidefinite cone under the afine function   
$f : \mathbf { R } ^ { n } \to \mathbf { S } ^ { m }$ given by $f ( x ) = B - A ( x )$

Example 2.11 Hyperbolic cone. The set

$$
\{ x \mid x ^ { T } P x \leq ( c ^ { T } x ) ^ { 2 } , c ^ { T } x \geq 0 \}
$$

where $P \in \mathbf { S } _ { + } ^ { n }$ and $c \in \mathbf { R } ^ { n }$ , is convex, since it
is the inverse image of the second-order cone,

$$
\{ ( z , t ) \mid z ^ { T } z \leq t ^ { 2 } , t \geq 0 \} ,
$$

under the afine function $f ( x ) = ( P ^ { 1 / 2 } x , c ^ { T } x )$

Example 2.12 Ellipsoid. The ellipsoid

$$
\mathcal { E } = \{ x \mid ( x - x _ { c } ) ^ { T } P ^ { - 1 } ( x - x _ { c } ) \leq 1 \} ,
$$

where $P \in \mathbf { S } _ { + + } ^ { n } ,$ is the image of the unit Euclidean ball
$\{ u \mid \| u \| _ { 2 } \leq 1 \}$ under the afine mapping
$f ( u ) = P ^ { 1 / 2 } u + x _ { c }$ . (It is also the inverse image of the unit ball under the
afine mapping $g ( x ) = P ^ { - 1 / 2 } ( x - x _ { c } ) . $)

## 2.3.3 Linear-fractional and perspective functions

In this section we explore a class of functions, called linear-fractional, that is more general than
afine but still preserves convexity.

## The perspective function

We define the perspective function $P : \mathbf { R } ^ { n + 1 }  \to \mathbf { R } ^ { n }$ , with
domain $\mathbf{dom} P = \mathbf { R } ^ { n } \times \mathbf { R } _ { + + }$ , as
$P ( z , t ) = z / t$ . (Here $\mathbf { R } _ { + + }$ denotes the set of positive numbers:
$\mathbf { R } _ { + + } = \{ x \in \mathbf { R } \mid x > 0 \} . $) The perspective function scales
or normalizes vectors so the last component is one, and then drops the last component.

Remark 2.1 We can interpret the perspective function as the action of a pin-hole camera. A pin-hole
camera (in $\mathbf { R } ^ { 3 } $) consists of an opaque horizontal plane $x _ { 3 } = 0 ,$ with a
single pin-hole at the origin, through which light can pass, and a horizontal image plane
$x _ { 3 } = - 1$ . An object at $x$, above the camera $( i . e . ,$ with $x _ { 3 } > 0 )$ , forms
an image at the point $- ( x _ { 1 } / x _ { 3 } , x _ { 2 } / x _ { 3 } , 1 )$ on the image plane.
Dropping the last component of the image point (since it is always 1), the image of a point at $x$
appears at $y = - ( x _ { 1 } / x _ { 3 } , x _ { 2 } / x _ { 3 } ) = - P ( x )$ on the image plane.
This is illustrated in figure 2.15.

If $C \subseteq \mathbf{dom} P$ is convex, then its image

$$
P ( C ) = \{ P ( x ) \mid x \in C \}
$$

is convex. This result is certainly intuitive: a convex object, viewed through a pin-hole camera,
yields a convex image. To establish this fact we show that line segments are mapped to line segments
under the perspective function. (This too makes sense: a line segment, viewed through a pin-hole
camera, yields a line segment image.) Suppose that
$x = ( \tilde { x } , x _ { n + 1 } ) , y = ( \tilde { y } , y _ { n + 1 } ) \in \mathbf { R } ^ { n + 1 }$
with $x _ { n + 1 } > 0$ $y _ { n + 1 } > 0$ . Then for $0 \leq \theta \leq 1$ ，

$$
P ( \theta x + ( 1 - \theta ) y ) = { \frac { \theta { \tilde { x } } + ( 1 - \theta ) { \tilde { y } } } { \theta x _ { n + 1 } + ( 1 - \theta ) y _ { n + 1 } } } = \mu P ( x ) + ( 1 - \mu ) P ( y ) ,
$$

where

$$
\mu = \frac { \theta x _ { n + 1 } } { \theta x _ { n + 1 } + ( 1 - \theta ) y _ { n + 1 } } \in [ 0 , 1 ] .
$$

This correspondence between $\theta$ and $\mu$ is monotonic: as $\theta$ varies between 0 and 1
(which sweeps out the line segment $[ x , y ] $), $\mu$ varies between 0 and 1 (which sweeps out the
line segment $[ P ( x ) , P ( y ) ] $) . This shows that $P ( [ x , y ] ) = [ P ( x ) , P ( y ) ]$

Now suppose $C$ is convex with $C \subseteq \mathbf{dom} P \ ( i . e . , x _ { n + 1 } > 0$ for all
$x \in C )$ , and $x ,  y \in C$ . To establish convexity of $P ( C )$ we need to show that the line
segment $[ P ( x ) , P ( y ) ]$ is in $P ( C )$ . But this line segment is the image of the line
segment [x, y] under $P$, and so lies in $P ( C )$

The inverse image of a convex set under the perspective function is also convex: if
$C \subseteq \mathbf { R } ^ { n }$ is convex, then

$$
P ^ { - 1 } ( C ) = \{ ( x , t ) \in \mathbf { R } ^ { n + 1 } \mid x / t \in C ,  t > 0 \}
$$

is convex. To show this, suppose $( x , t ) \in P ^ { - 1 } ( C ) , ( y , s ) \in P ^ { - 1 } ( C )$ ,
and $0 \leq \theta \leq 1$ We need to show that

$$
\theta ( x , t ) + ( 1 - \theta ) ( y , s ) \in P ^ { - 1 } ( C ) ,
$$

i.e., that

$$
{ \frac { \theta x + ( 1 - \theta ) y } { \theta t + ( 1 - \theta ) s } } \in C
$$

($ \theta t + ( 1 - \theta ) s > 0$ is obvious). This follows from

$$
\frac { \theta x + ( 1 - \theta ) y } { \theta t + ( 1 - \theta ) s } = \mu ( x / t ) + ( 1 - \mu ) ( y / s ) ,
$$

where

$$
\mu = \frac { \theta t } { \theta t + ( 1 - \theta ) s } \in [ 0 , 1 ] .
$$

## Linear-fractional functions

A linear-fractional function is formed by composing the perspective function with an afine function.
Suppose $g : \mathbf { R } ^ { n } \to \mathbf { R } ^ { m + 1 }$ is affine,  $i. e .$

$$
g ( x ) = \left[ { \begin{array} { l } { A } \\ { c ^ { T } } \end{array} } \right] x + \left[ { \begin{array} { l } { b } \\ { d } \end{array} } \right] ,\tag{2.12}
$$

where $A \in \mathbf { R } ^ { m \times n } , b \in \mathbf { R } ^ { m } , c \in \mathbf { R } ^ { n }$ ,
and $d \in \mathbf { R }$ . The function $f : \mathbf { R } ^ { n } \to \mathbf { R } ^ { m }$ given
by $f = P \circ g , i . e .$

$$
f ( x ) = ( A x + b ) / ( c ^ { T } x + d ) , \qquad \mathbf { dom } f = \{ x \mid c ^ { T } x + d > 0 \} ,\tag{2.13}
$$

is called a linear-fractional (or projective) function. If $c = 0$ and $d > 0$ , the domain of $f$
is $\mathbf { R } ^ { n }$ , and $f$ is an afine function. So we can think of afine and linear
functions as special cases of linear-fractional functions.

Remark 2.2 Projective interpretation. It is often convenient to represent a linearfractional
function as a matrix

$$
Q = { \left[ \begin{array} { l l } { A } & { b } \\ { c ^ { T } } & { d } \end{array} \right] } \in \mathbf { R } ^ { ( m + 1 ) \times ( n + 1 ) }\tag{2.14}
$$

that acts on (multiplies) points of form $( x , 1 )$ , which yields
$( A x + b , c ^ { T } x + d )$ . This result is then scaled or normalized so that its last
component is one, which yields $( f ( x ) , 1 )$

This representation can be interpreted geometrically by associating $\mathbf { R } ^ { n }$ with a
set of rays in $\mathbf { R } ^ { n + 1 }$ as follows. With each point z in $\mathbf { R } ^ { n }$
we associate the (open) ray $\mathcal { P } ( z ) = \{ t ( z , 1 ) \mid t > 0 \}$ in
$\mathbf { R } ^ { n + 1 }$ . The last component of this ray takes on positive values. Conversely
any ray in $\mathbf { R } ^ { n + 1 }$ , with base at the origin and last component which takes on
positive values, can be written as $\mathcal { P } ( v ) = \{ t ( v , 1 ) \mid t \geq 0 \}$ for some
$v \in \mathbf { R } ^ { n }$ . This (projective) correspondence $\mathcal { P }$ between
$\mathbf { R } ^ { n }$ and the halfspace of rays with positive last component is one-to-one and
onto.

The linear-fractional function (2.13) can be expressed as

$$
f ( x ) = { \mathcal { P } } ^ { - 1 } ( Q { \mathcal { P } } ( x ) ) .
$$

Thus, we start with $x \in$ dom $f , \ i . e . , \ c ^ { T } x + d > 0$ . We then form the ray
${ \mathcal { P } } ( x )$ in $\mathbf { R } ^ { n + 1 }$ . The linear transformation with matrix
$Q$ acts on this ray to produce another ray $Q P ( x )$ . Since $x \in \mathbf { d o m } f ,$ the
last component of this ray assumes positive values. Finally we take the inverse projective
transformation to recover $f ( x )$

Like the perspective function, linear-fractional functions preserve convexity. If C is convex and
lies in the domain of $f  ( i . e . , c ^ { T } x + d > 0$ for $x \in C )$ , then its image
$f ( C )$ is convex. This follows immediately from results above: the image of C under the afine
mapping (2.12) is convex, and the image of the resulting set under the perspective function $P ,$
which yields $f ( C )$ , is convex. Similarly, if ${ \cal { C } } \subseteq \mathbf { R } ^ { m }$
is convex, then the inverse image $f ^ { - 1 } ( C )$ is convex.

Example 2.13 Conditional probabilities. Suppose u and v are random variables that take on values in
$\{ 1 , \ldots , n \}$ and $\{ 1 , \ldots , m \}$ , respectively, and let $p _ { i j }$ denote
$\mathbf { prob } ( u = i , v = j )$ . Then the conditional probability
$f _ { i j } = \mathbf {prob} ( u = i | v = j )$ is given by

$$
f _ { i j } = { \frac { p _ { i j } } { \sum _ { k = 1 } ^ { n } p _ { k j } } } .
$$

Thus $f$ is obtained by a linear-fractional mapping from $p .$

It follows that if C is a convex set of joint probabilities for $( u , v )$ , then the associated
set of conditional probabilities of u given v is also convex.

Figure 2.16 shows a set $C \subseteq \mathbf { R } ^ { 2 }$ , and its image under the
linear-fractional function

$$
f ( x ) = { \frac { 1 } { x _ { 1 } + x _ { 2 } + 1 } } x , \qquad \mathbf { dom } f = \{ ( x _ { 1 } , x _ { 2 } ) \mid x _ { 1 } + x _ { 2 } + 1 > 0 \} .
$$
